/** @odoo-module */

import { _t } from "@web/core/l10n/translation";
import { PaymentInterface } from "@point_of_sale/app/payment/payment_interface";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { register_payment_method } from "@point_of_sale/app/store/pos_store";
import { NewLeapWaitingPopup } from "./newleap_waiting_popup";

/**
 * PaymentNewLeap - Payment terminal implementation for NewLeap mobile payments.
 *
 * Flow:
 * 1. User selects NewLeap payment and clicks Validate
 * 2. send_payment_request() is called, returns a Promise
 * 3. Backend sends request to middleware, which broadcasts to mobile app
 * 4. Mobile app processes payment and sends callback to middleware
 * 5. Middleware forwards to Odoo backend, which sends bus notification
 * 6. handleNewLeapStatusResponse() is called, resolves the pending Promise
 * 7. Order validation continues
 */
export class PaymentNewLeap extends PaymentInterface {
    setup() {
        super.setup(...arguments);
        // Store resolvers for pending payment promises, keyed by payment line UUID
        this.paymentLineResolvers = {};
        this.waitingPopupClosers = {};
    }

    /**
     * Called when payment is initiated (user clicks Validate or Send)
     * @param {string} uuid - The payment line UUID
     * @returns {Promise<boolean>} - Resolves when payment is confirmed/rejected
     */
    send_payment_request(uuid) {
        super.send_payment_request(uuid);
        return this._newleap_pay(uuid);
    }

    /**
     * Called when user cancels a pending payment
     * @param {Object} order - The current order
     * @param {string} uuid - The payment line UUID
     * @returns {Promise<boolean>}
     */
    send_payment_cancel(order, uuid) {
        super.send_payment_cancel(order, uuid);
        return this._newleap_cancel(uuid);
    }

    /**
     * Get the currently pending NewLeap payment line
     * @returns {Object|undefined} - The pending payment line or undefined
     */
    pending_newleap_line() {
        return this.pos.getPendingPaymentLine("newleap");
    }

    /**
     * Handle connection failure to Odoo server
     * @param {Object} data - Error data
     * @returns {Promise<boolean>} - Rejects with error data
     */
    _handle_odoo_connection_failure(data = {}) {
        const line = this.pending_newleap_line();
        if (line) {
            line.set_payment_status("retry");
        }
        this._show_error(
            _t("Could not connect to the Odoo server. Please check your internet connection and try again.")
        );
        return Promise.reject(data);
    }

    /**
     * Initiate payment request to NewLeap via middleware
     * @param {string} uuid - The payment line UUID
     * @returns {Promise<boolean>} - Resolves when payment is confirmed
     */
    async _newleap_pay(uuid) {
        const order = this.pos.get_order();
        const line = order.payment_ids.find((paymentLine) => paymentLine.uuid === uuid);

        if (!line) {
            this._show_error(_t("Payment line not found."));
            return false;
        }

        if (line.amount <= 0) {
            this._show_error(_t("Cannot process payments with zero or negative amount."));
            return false;
        }

        // Set status to waiting (shows "Request sent" spinner)
        line.set_payment_status("waiting");

        console.log("[NewLeap] Initiating payment:", {
            pos_reference: order.name,
            amount: line.amount,
            session_id: order.session_id?.id,
        });

        try {
            // Call Odoo backend to initiate payment
            const result = await this.pos.data.silentCall(
                "pos.payment.method",
                "initiate_newleap_payment",
                [
                    [this.payment_method_id.id],
                    order.name,  // pos_reference
                    line.amount,
                    order.session_id?.id || this.pos.session.id,
                ]
            );

            console.log("[NewLeap] Backend response:", result);

            if (result && result.result_type === "success") {
                // Payment request sent successfully, now wait for mobile confirmation
                line.set_payment_status("waitingCard");
                console.log("[NewLeap] Waiting for payment confirmation...");
                this._openWaitingPopup(line);
                return this.waitForPaymentConfirmation(uuid);
            } else {
                const errorMsg = result?.message || _t("Unknown error");
                this._show_error(_t("Failed to initiate payment: %s", errorMsg));
                line.set_payment_status("retry");
                return false;
            }
        } catch (error) {
            console.error("[NewLeap] Error initiating payment:", error);
            return this._handle_odoo_connection_failure(error);
        }
    }

    /**
     * Handle cancel request - NewLeap payments must be cancelled from mobile app
     * @param {string} uuid - The payment line UUID
     * @returns {Promise<boolean>}
     */
    async _newleap_cancel(uuid) {
        // Remove the resolver if exists
        if (this.paymentLineResolvers[uuid]) {
            this.paymentLineResolvers[uuid](false);
            delete this.paymentLineResolvers[uuid];
        }
        this._closeWaitingPopup(uuid);

        const line = this.pending_newleap_line();
        if (line) {
            line.set_payment_status("retry");
        }

        this._show_error(
            _t("Payment cancelled. Please complete or cancel the payment on the mobile app if it's still pending.")
        );
        return true;
    }

    /**
     * Creates a Promise that will be resolved when payment confirmation is received.
     * The resolver is stored so it can be called later by handleNewLeapStatusResponse().
     *
     * @param {string} uuid - The payment line UUID
     * @returns {Promise<boolean>} - Resolves with true on success, false on failure
     */
    waitForPaymentConfirmation(uuid) {
        return new Promise((resolve) => {
            this.paymentLineResolvers[uuid] = resolve;
            console.log("[NewLeap] Resolver stored for UUID:", uuid);
        });
    }
    _openWaitingPopup(line) {
        if (!line || this.waitingPopupClosers[line.uuid]) {
            return;
        }
        const order = line.pos_order_id;
        const amount = this.env.utils.formatCurrency(line.get_amount());
        const close = this.env.services.dialog.add(
            NewLeapWaitingPopup,
            {
                order,
                amount,
                body: _t(
                    "Confirm the payment in the NewLeap mobile app. The order will validate automatically."
                ),
                confirm: () => this._newleap_cancel(line.uuid),
            },
            {
                onClose: () => {
                    delete this.waitingPopupClosers[line.uuid];
                },
            }
        );
        this.waitingPopupClosers[line.uuid] = close;
    }
    _closeWaitingPopup(uuid) {
        const close = this.waitingPopupClosers?.[uuid];
        if (close) {
            close();
            delete this.waitingPopupClosers[uuid];
        }
    }

    /**
     * Called from pos_store.js when bus notification is received.
     * Retrieves the latest payment status and resolves the pending promise.
     */
    async handleNewLeapStatusResponse(notification, line) {
        console.log("[NewLeap] Handling status response...");

        const pendingLine = line || this.pending_newleap_line();
        if (!pendingLine) {
            console.warn("[NewLeap] No pending payment line found");
            return;
        }

        let resolvedNotification = notification;
        if (!resolvedNotification) {
            // Get the latest status from backend as fallback
            resolvedNotification = await this.pos.data.silentCall(
                "pos.payment.method",
                "get_latest_newleap_status",
                [[this.payment_method_id.id]]
            );
        }

        console.log("[NewLeap] Latest status:", resolvedNotification);

        if (!resolvedNotification) {
            console.warn("[NewLeap] No notification data received");
            return;
        }

        const order = pendingLine.pos_order_id;
        if (
            resolvedNotification.pos_order &&
            order?.name &&
            resolvedNotification.pos_order !== order.name
        ) {
            console.warn(
                "[NewLeap] Ignoring notification for different order:",
                resolvedNotification.pos_order
            );
            return;
        }

        const lastTxnRef = order?.uiState?.newleap_last_transaction_reference;
        const lastStatus = order?.uiState?.newleap_last_status;
        if (
            resolvedNotification.transaction_reference &&
            resolvedNotification.status &&
            lastTxnRef === resolvedNotification.transaction_reference &&
            lastStatus === resolvedNotification.status
        ) {
            console.log(
                "[NewLeap] Duplicate notification ignored:",
                resolvedNotification.transaction_reference
            );
            return;
        }
        if (order?.uiState) {
            if (resolvedNotification.transaction_reference) {
                order.uiState.newleap_last_transaction_reference =
                    resolvedNotification.transaction_reference;
            }
            if (resolvedNotification.status) {
                order.uiState.newleap_last_status = resolvedNotification.status;
            }
        }

        const isPaymentSuccessful = resolvedNotification.status === "paid";

        if (isPaymentSuccessful) {
            // Store transaction reference on the payment line
            const transactionRef = resolvedNotification.transaction_reference || "";
            pendingLine.transaction_id = transactionRef;
            pendingLine.card_type = "NewLeap";
            if (transactionRef && order) {
                order.update({ transaction_reference: transactionRef });
            }
            console.log(
                "[NewLeap] Payment successful, transaction:",
                resolvedNotification.transaction_reference
            );
        } else {
            const errorMsg =
                resolvedNotification.message ||
                resolvedNotification.error_message ||
                resolvedNotification.detail ||
                resolvedNotification.reason ||
                _t("Payment was not successful");
            this._show_error(errorMsg);
            console.log("[NewLeap] Payment failed:", errorMsg);
        }
        this._closeWaitingPopup(pendingLine.uuid);

        // Resolve the pending promise
        const resolver = this.paymentLineResolvers?.[pendingLine.uuid];
        if (resolver) {
            console.log("[NewLeap] Resolving promise for UUID:", pendingLine.uuid);
            resolver(isPaymentSuccessful);
            delete this.paymentLineResolvers[pendingLine.uuid];
        } else {
            // Handle case where resolver is lost (e.g., page refresh during payment)
            console.log("[NewLeap] No resolver found, using handle_payment_response fallback");
            pendingLine.handle_payment_response(isPaymentSuccessful);
        }
    }

    /**
     * Show error dialog to user
     * @param {string} msg - Error message
     * @param {string} title - Dialog title (optional)
     */
    _show_error(msg, title) {
        if (!title) {
            title = _t("NewLeap Payment Error");
        }
        this.env.services.dialog.add(AlertDialog, {
            title: title,
            body: msg,
        });
    }
}

// Register the payment method with the POS store
register_payment_method("newleap", PaymentNewLeap);
