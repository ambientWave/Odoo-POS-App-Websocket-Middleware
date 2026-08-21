import logging
import json
import requests

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class PosPaymentMethod(models.Model):
    _inherit = 'pos.payment.method'

    is_newleap_journal = fields.Boolean(string="NewLeap Payment", default=False)

    # NewLeap payment terminal fields
    newleap_latest_response = fields.Char(
        string="Latest NewLeap Response",
        copy=False,
        groups='base.group_erp_manager',
        help="Stores the latest payment response for frontend retrieval"
    )
    newleap_middleware_url = fields.Char(
        string="Middleware URL",
        help="URL of the NewLeap middleware service (e.g., http://localhost:6000)",
        default="http://localhost:6000"
    )

    def _get_payment_terminal_selection(self):
        """Add NewLeap to payment terminal selection."""
        return super()._get_payment_terminal_selection() + [('newleap', 'NewLeap')]

    @api.model
    def _load_pos_data_fields(self, config_id):
        """Override to include NewLeap fields in POS data."""
        fields_list = super()._load_pos_data_fields(config_id)
        fields_list.append('is_newleap_journal')
        return fields_list

    def _is_write_forbidden(self, fields):
        """Allow writing newleap_latest_response during active session."""
        return super()._is_write_forbidden(fields - {'newleap_latest_response'})

    def get_latest_newleap_status(self):
        """Called from frontend to get the latest payment status."""
        self.ensure_one()
        latest_response = self.sudo().newleap_latest_response
        if latest_response:
            try:
                return json.loads(latest_response)
            except json.JSONDecodeError:
                return False
        return False

    def initiate_newleap_payment(self, pos_reference, amount_total, session_id):
        """Send payment request to NewLeap middleware.

        Args:
            pos_reference: The POS order reference (e.g., "Order 00001-001-0001")
            amount_total: The payment amount
            session_id: The POS session ID

        Returns:
            dict: Response from middleware with result_type and message
        """
        self.ensure_one()
        middleware_url = self.sudo().newleap_middleware_url or 'http://localhost:6000'

        # Clear previous response
        self.sudo().newleap_latest_response = ''

        _logger.info(
            "[NewLeap] Initiating payment: pos_reference=%s, amount=%s, session=%s, url=%s",
            pos_reference, amount_total, session_id, middleware_url
        )

        # Get session and config for bus notification
        session = self.env['pos.session'].sudo().browse(session_id)
        currency = session.currency_id.name if session else 'USD'

        try:
            response = requests.post(
                f"{middleware_url}/api/newleap/pos/order/new",
                json={
                    "token": str(session_id), # TODO: name ambiguity. token isn't the session id
                    "pos_reference": pos_reference,
                    "amount": amount_total,
                    "currency": currency,
                },
                timeout=10,
                verify=False,
            )
            result = response.json()
            _logger.info("[NewLeap] Middleware response: %s", result)

            # Emit monitoring event: payment sent to mobile
            if result.get('result_type') == 'success' and session.config_id:
                monitor_event = {
                    'event': 'payment_initiated',
                    'timestamp': fields.Datetime.now().isoformat(),
                    'order_id': pos_reference,
                    'invoice_id': pos_reference,
                    'amount': amount_total,
                    'currency': currency,
                    'status': 'sent_to_mobile',
                    'session_id': session_id,
                }
                session.config_id._notify("NEWLEAP_MONITOR", monitor_event)
                _logger.info("[NewLeap Monitor] Payment initiated: %s", monitor_event)

            return result
        except requests.exceptions.Timeout:
            _logger.error("[NewLeap] Middleware request timed out")
            return {"result_type": "error", "message": "Connection timed out"}
        except requests.exceptions.ConnectionError as e:
            _logger.error("[NewLeap] Failed to connect to middleware: %s", e)
            return {"result_type": "error", "message": "Could not connect to payment service"}
        except Exception as e:
            _logger.exception("[NewLeap] Unexpected error initiating payment")
            return {"result_type": "error", "message": str(e)}

    @api.model_create_multi
    def create(self, vals_list):
        """Override create to log when NewLeap payment method is created."""
        records = super().create(vals_list)
        for record in records:
            if record.is_newleap_journal or record.use_payment_terminal == 'newleap':
                _logger.info(
                    "pos.payment.method [CREATE] - NewLeap Payment Method Created: "
                    "ID=%s, Name=%s, is_newleap_journal=%s, use_payment_terminal=%s",
                    record.id, record.name, record.is_newleap_journal, record.use_payment_terminal
                )
        return records

    def write(self, vals):
        """Override write to log when NewLeap payment method is modified."""
        res = super().write(vals)
        for record in self:
            if record.is_newleap_journal or record.use_payment_terminal == 'newleap':
                _logger.info(
                    "pos.payment.method [WRITE] - NewLeap Payment Method Updated: "
                    "ID=%s, Name=%s, is_newleap_journal=%s, use_payment_terminal=%s",
                    record.id, record.name, record.is_newleap_journal, record.use_payment_terminal
                )
        return res
