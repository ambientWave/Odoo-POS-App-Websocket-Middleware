/** @odoo-module */

/**
 * Payment Screen patch for NewLeap integration.
 *
 * Note: The main payment logic is handled by PaymentNewLeap (payment_newleap.js)
 * which implements the PaymentInterface pattern. This file adds:
 * - Auto-validation for NewLeap payments even if the POS setting is disabled
 * - Showing the receipt screen after successful payment
 * - Optional debug logging for NewLeap payments
 *
 * The PaymentInterface automatically handles:
 * - Initiating payment when user clicks Validate
 * - Showing loading state ("Waiting for card")
 * - Waiting for bus notification from backend
 * - Completing the order when payment is confirmed
 */

import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { patch } from "@web/core/utils/patch";

patch(PaymentScreen.prototype, {
    _isNewLeapPaymentLine(line) {
        const method = line?.payment_method_id;
        return (
            method?.use_payment_terminal === "newleap" ||
            method?.is_newleap_journal
        );
    },
    _isNewLeapOrder(order) {
        const targetOrder = order || this.currentOrder;
        return targetOrder?.payment_ids?.some((line) => this._isNewLeapPaymentLine(line));
    },
    async sendPaymentRequest(line) {
        await super.sendPaymentRequest(...arguments);
        if (!this._isNewLeapPaymentLine(line)) {
            return;
        }
        if (this.pos.config.auto_validate_terminal_payment) {
            return;
        }
        const order = line.pos_order_id;
        if (order && order.is_paid() && !order.finalized) {
            this.validateOrder(false);
        }
    },
    async sendForceDone(line) {
        await super.sendForceDone(...arguments);
        if (!this._isNewLeapPaymentLine(line)) {
            return;
        }
        if (this.pos.config.auto_validate_terminal_payment) {
            return;
        }
        const order = line.pos_order_id;
        if (order && order.is_paid() && !order.finalized) {
            this.validateOrder(true);
        }
    },
    get nextScreen() {
        const order = this.currentOrder;
        if (order && order.is_paid() && this._isNewLeapOrder(order)) {
            return "ReceiptScreen";
        }
        return super.nextScreen;
    },
    async afterOrderValidation() {
        const paidOrder = this.currentOrder;
        const isNewLeapOrder = this._isNewLeapOrder(paidOrder);
        const nextScreen = this.nextScreen;
        if (
            isNewLeapOrder &&
            !this.error &&
            nextScreen !== "ReceiptScreen" &&
            paidOrder.nb_print === 0 &&
            this.pos.config.iface_print_auto
        ) {
            const invoicedFinalized = paidOrder.is_to_invoice()
                ? paidOrder.finalized
                : true;
            if (invoicedFinalized) {
                this.pos.printReceipt({ order: paidOrder });
            }
        }

        await super.afterOrderValidation(...arguments);

        if (!isNewLeapOrder) {
            return;
        }

        const shouldSwitch = paidOrder.uuid === this.pos.selectedOrderUuid;
        if (shouldSwitch) {
            this.pos.showScreen("ReceiptScreen");
        }
    },
    /**
     * Override addNewPaymentLine to log when NewLeap payment method is selected.
     * The actual payment flow is handled by PaymentNewLeap.send_payment_request().
     *
     * @param {Object} paymentMethod - The selected payment method
     * @returns {Boolean} - Result from parent method
     */
    addNewPaymentLine(paymentMethod) {
        // Log when NewLeap payment terminal is selected
        if (paymentMethod.use_payment_terminal === 'newleap') {
            console.log(
                "[NewLeap] Payment method selected:",
                "ID:", paymentMethod.id,
                "Name:", paymentMethod.name,
                "Terminal:", paymentMethod.use_payment_terminal
            );
        }
        return super.addNewPaymentLine(paymentMethod);
    },
});
