/** @odoo-module */

import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";

export class NewLeapWaitingPopup extends ConfirmationDialog {
    static template = "intgration_newleap.NewLeapWaitingPopup";
    static props = {
        ...ConfirmationDialog.props,
        order: { type: Object, optional: true },
        amount: { type: String, optional: true },
    };
    static defaultProps = {
        ...ConfirmationDialog.defaultProps,
        title: _t("NewLeap Payment"),
        confirmLabel: _t("Cancel Payment"),
        cancelLabel: _t("Hide"),
    };

    setup() {
        super.setup();
        this.props.body =
            this.props.body ||
            _t("Waiting for confirmation on the NewLeap mobile app.");
    }
}
