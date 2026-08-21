{
    'name': 'Integration NewLeap',
    'version': '18.0.1.0.0',
    'category': 'Point of Sale',
    'summary': 'NewLeap Payment Integration for POS',
    'description': """
        This module integrates NewLeap payment method with Point of Sale.

        Features:
        - NewLeap payment terminal integration
        - Async payment flow with mobile app confirmation
        - Real-time payment status updates via Odoo bus
    """,
    'author': 'Basem Walid, Mina Wahib Gebrail',
    'depends': ['point_of_sale'],
    'data': [
        'views/pos_payment_method_views.xml',
        'views/pos_order_views.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'intgration_newleap/static/src/js/newleap_waiting_popup.js',
            'intgration_newleap/static/src/js/payment_newleap.js',
            'intgration_newleap/static/src/js/pos_store.js',
            'intgration_newleap/static/src/js/payment_screen.js',
            'intgration_newleap/static/src/xml/newleap_waiting_popup.xml',
            'intgration_newleap/static/src/xml/payment_lines_newleap.xml',
        ],
    },
    'installable': True,
    'application': True,
    'auto_install': False,
    'license': 'LGPL-3',
}
