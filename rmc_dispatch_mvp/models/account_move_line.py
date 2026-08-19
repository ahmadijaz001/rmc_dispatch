from odoo import fields, models


class AccountMoveLine(models.Model):
    """ Extension: traceability from an invoice line back to the RMC load it bills. """
    _inherit = 'account.move.line'

    rmc_load_id = fields.Many2one(
        'rmc.load', string='RMC Load', index=True, ondelete='set null', copy=False,
        help="Truck load this invoice line was generated from, if any.")
