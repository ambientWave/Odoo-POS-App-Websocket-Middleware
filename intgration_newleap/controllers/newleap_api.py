import logging
import json

from odoo import http
from odoo.http import request, Response
from .auth import authenticate_token

_logger = logging.getLogger(__name__)


class AuthOnlyAPI(http.Controller):

    @http.route(
        '/api/verify_token',
        type='http',
        auth='none',
        methods=['POST'],
        csrf=False
    )
    def verify_token(self, **kw):
        try:
            token = request.httprequest.headers.get("Authorization")

            if not token or not token.startswith("Bearer "):
                return request.make_json_response({
                    "result_type": "failure",
                    "message": "Missing or invalid Authorization header"
                }, status=401)

            auth = authenticate_token(token.replace("Bearer ", ""))

            if auth.get("result_type") != "success":
                return request.make_json_response({
                    "result_type": "failure",
                    "message": "Unauthorized"
                }, status=401)

            user_id = int(auth["result"]["user_id"])
            request.update_env(user=user_id)

            return request.make_json_response({
                "result_type": "success",
                "result": {
                    "user_id": user_id
                }
            })

        except Exception as e:
            _logger.exception("Auth verify failed")
            return request.make_json_response({
                "result_type": "failure",
                "message": str(e)
            }, status=500)


class NewLeapPaymentController(http.Controller):

    @http.route(
        '/api/newleap/payment',
        type='http',
        auth='none',
        methods=['POST'],
        csrf=False
    )
    def payment_callback(self, **kwargs):
        """
        API endpoint to receive payment callback from NewLeap middleware.

        Expected JSON payload:
        {
            "pos_order": "Order 00001-001-0001",  # pos_reference
            "transaction_reference": "NL-TXN-123456",
            "status": "paid"  # or "failed"
        }

        Returns:
        {
            "success": true/false,
            "message": "...",
            "order_name": "POS/001/0001"
        }
        """
        try:
            # Get JSON data from request body (plain HTTP, not JSON-RPC)
            data = json.loads(request.httprequest.data.decode('utf-8'))

            pos_order_ref = data.get('pos_order')
            transaction_reference = data.get('transaction_reference')
            status = data.get('status')
            message = data.get('message')
            error_message = data.get('error_message')
            detail = data.get('detail')
            if not message and error_message:
                message = error_message

            _logger.info(
                "[NewLeap API] Payment callback received: pos_order=%s, "
                "transaction_reference=%s, status=%s",
                pos_order_ref, transaction_reference, status
            )

            # Validate required fields
            if not pos_order_ref:
                return request.make_json_response({
                    'success': False,
                    'message': 'Missing required field: pos_order'
                }, status=400)

            if not transaction_reference:
                return request.make_json_response({
                    'success': False,
                    'message': 'Missing required field: transaction_reference'
                }, status=400)

            if not status:
                return request.make_json_response({
                    'success': False,
                    'message': 'Missing required field: status'
                }, status=400)

            notification_data = {
                'pos_order': pos_order_ref,
                'transaction_reference': transaction_reference,
                'status': status,
            }
            if message:
                notification_data['message'] = message
            if error_message:
                notification_data['error_message'] = error_message
            if detail and 'message' not in notification_data:
                notification_data['message'] = detail

            # Search for the POS order by pos_reference or name
            # Note: Order might not exist yet (POS orders are created locally first)
            PosOrder = request.env['pos.order'].sudo()
            order = PosOrder.search([('pos_reference', '=', pos_order_ref)], limit=1)

            if not order:
                # Try by name as fallback
                order = PosOrder.search([('name', '=', pos_order_ref)], limit=1)

            if order:
                # Update transaction_reference if order exists
                order.write({
                    'transaction_reference': transaction_reference
                })
                _logger.info(
                    "[NewLeap API] Order found and updated: %s", order.name
                )

            # Find ALL NewLeap payment methods to send notification
            # (order might not exist yet, so we broadcast to all active POS configs)
            payment_methods = request.env['pos.payment.method'].sudo().search([
                ('use_payment_terminal', '=', 'newleap')
            ])

            if payment_methods:
                for payment_method in payment_methods:
                    # Store the response for the frontend to retrieve
                    payment_method.newleap_latest_response = json.dumps(notification_data)

                # Send bus notification to ALL POS configs that use NewLeap
                from odoo import fields as odoo_fields
                for payment_method in payment_methods:
                    for config in payment_method.config_ids:
                        try:
                            # Send payment response notification
                            config._notify(
                                "NEWLEAP_PAYMENT_RESPONSE",
                                notification_data
                            )

                            # Send monitoring event: payment confirmed
                            monitor_event = {
                                'event': 'payment_confirmed',
                                'timestamp': odoo_fields.Datetime.now().isoformat(),
                                'order_id': pos_order_ref,
                                'invoice_id': pos_order_ref,
                                'amount': order.amount_total if order else None,
                                'currency': order.currency_id.name if order else None,
                                'status': 'success' if status.lower() == 'paid' else 'failed',
                                'transaction_reference': transaction_reference,
                                'order_found': bool(order),
                            }
                            config._notify("NEWLEAP_MONITOR", monitor_event)

                            _logger.info(
                                "[NewLeap API] Bus notification sent to config %s for order %s",
                                config.id, pos_order_ref
                            )
                            _logger.info(
                                "[NewLeap Monitor] Payment confirmed: %s", monitor_event
                            )
                        except Exception as e:
                            _logger.warning(
                                "[NewLeap API] Failed to send notification to config %s: %s",
                                config.id, e
                            )
            else:
                _logger.warning(
                    "[NewLeap API] No NewLeap payment methods found"
                )

            return request.make_json_response({
                'success': True,
                'message': f'Payment notification sent for {pos_order_ref}',
                'order_found': bool(order),
                'order_name': order.name if order else None,
                'transaction_reference': transaction_reference,
                'status': status,
                'message_detail': notification_data.get('message')
            })

        except Exception as e:
            _logger.exception("[NewLeap API] Error processing payment callback")
            return request.make_json_response({
                'success': False,
                'message': str(e)
            }, status=500)

    @http.route(
        '/api/newleap/payment/callback',
        type='json',
        auth='public',
        methods=['POST'],
        csrf=False
    )
    def payment_callback_legacy(self, **kwargs):
        """
        Legacy endpoint for backward compatibility.
        Redirects to the main payment callback handler.
        """
        # Map old field names to new ones if needed
        data = request.jsonrequest
        if 'order_id' in data and 'pos_order' not in data:
            data['pos_order'] = data['order_id']
        return self.payment_callback(**kwargs)
