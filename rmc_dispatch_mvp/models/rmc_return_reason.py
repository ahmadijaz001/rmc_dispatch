from odoo import fields, models


class RmcReturnReason(models.Model):
    """ Reasons a truck load can be returned to plant, partially or fully undelivered. """
    _name = 'rmc.return.reason'
    _description = 'RMC Return Reason'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True, help="Reason shown to dispatchers and drivers.")
    code = fields.Char(help="Short internal code for reporting.")
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    chargeable = fields.Boolean(
        string='Chargeable to Customer',
        help="If set, a load returned with this reason can be invoiced a return/cancellation charge.")
    default_charge_product_id = fields.Many2one(
        'product.product', string='Default Charge Product',
        help="Service product used as the invoice line when this return reason is charged to the customer.")
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)

    _chargeable_needs_product = models.Constraint(
        "CHECK (chargeable IS NOT TRUE OR default_charge_product_id IS NOT NULL)",
        "A chargeable return reason must have a default charge product.",
    )
