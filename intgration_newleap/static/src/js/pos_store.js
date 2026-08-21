/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/store/pos_store";

/**
 * Patch PosStore to subscribe to NewLeap payment notifications.
 *
 * When the backend receives a payment callback from the middleware,
 * it sends a bus notification with type "NEWLEAP_PAYMENT_RESPONSE".
 * This patch subscribes to that notification and triggers the
 * PaymentNewLeap handler to resolve the pending payment promise.
 */
patch(PosStore.prototype, {
    _getPendingNewLeapLineForOrder(posOrderRef) {
        for (const order of this.models["pos.order"].getAll()) {
            if (posOrderRef && order.name !== posOrderRef) {
                continue;
            }
            const paymentLine = order.payment_ids.find(
                (line) =>
                    line.payment_method_id.use_payment_terminal === "newleap" &&
                    !line.is_done() &&
                    line.get_payment_status() !== "retry"
            );
            if (paymentLine) {
                return paymentLine;
            }
        }
    },
    async setup() {
        await super.setup(...arguments);

        // Subscribe to NewLeap payment notifications via Odoo bus
        this.data.connectWebSocket("NEWLEAP_PAYMENT_RESPONSE", (payload) => {
            console.log("[NewLeap PosStore] Received payment notification:", payload);

            // Find the pending NewLeap payment line for the matching order
            const pendingLine = this._getPendingNewLeapLineForOrder(payload?.pos_order);

            if (pendingLine) {
                // Get the payment terminal instance and handle the response
                const terminal = pendingLine.payment_method_id.payment_terminal;
                if (terminal && typeof terminal.handleNewLeapStatusResponse === "function") {
                    terminal.handleNewLeapStatusResponse(payload, pendingLine);
                } else {
                    console.warn("[NewLeap PosStore] Payment terminal not found or missing handler");
                }
            } else {
                console.log(
                    "[NewLeap PosStore] No pending NewLeap payment line found for order:",
                    payload?.pos_order || "-"
                );
            }
        });

        // Subscribe to NewLeap monitoring events (live log)
        this.data.connectWebSocket("NEWLEAP_MONITOR", (payload) => {
            const timestamp = payload.timestamp || new Date().toISOString();
            const event = payload.event || 'unknown';
            const status = payload.status || 'unknown';

            // Format log message like Odoo logs
            const logStyle = status === 'success' || status === 'sent_to_mobile'
                ? 'color: #28a745; font-weight: bold;'
                : status === 'failed'
                ? 'color: #dc3545; font-weight: bold;'
                : 'color: #17a2b8; font-weight: bold;';

            console.log(
                `%c[NewLeap Monitor] ${timestamp} | ${event.toUpperCase()}`,
                logStyle
            );
            console.table({
                'Order ID': payload.order_id,
                'Invoice ID': payload.invoice_id,
                'Amount': payload.amount,
                'Currency': payload.currency,
                'Status': payload.status,
                'Transaction Ref': payload.transaction_reference || '-',
                'Session ID': payload.session_id || '-',
            });
        });

        console.log("[NewLeap PosStore] Subscribed to NEWLEAP_PAYMENT_RESPONSE notifications");
        console.log("[NewLeap PosStore] Subscribed to NEWLEAP_MONITOR notifications (live log)");
    },
});
