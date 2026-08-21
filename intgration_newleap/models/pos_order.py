import logging

from odoo import api, models, fields

_logger = logging.getLogger(__name__)


class PosOrder(models.Model):
    _inherit = 'pos.order'

    transaction_reference = fields.Char(
        string="Transaction Reference",
        readonly=True,
        copy=False,
        help="NewLeap payment transaction reference"
    )

    def add_payment(self, data):
        """Override to log when NewLeap payment is added to an order."""
        res = super().add_payment(data)

        payment_method_id = data.get('payment_method_id')
        if payment_method_id:
            payment_method = self.env['pos.payment.method'].browse(payment_method_id)
            if payment_method.exists() and payment_method.is_newleap_journal:
                _logger.info(
                    "pos.order [ADD_PAYMENT] - NewLeap Payment Used: "
                    "Order=%s, Payment Method ID=%s, Payment Method Name=%s, "
                    "Amount=%s, is_newleap_journal=%s",
                    self.name,
                    payment_method.id,
                    payment_method.name,
                    data.get('amount'),
                    payment_method.is_newleap_journal
                )
        return res
