from odoo import fields, models


class SaleOrderLine(models.Model):
    """ Extension: RMC project/phase linkage and delivered-quantity reporting.

    Deliberately does NOT depend on sale_project - see README "Design decisions and
    assumptions" for why (avoids sale_project's auto-project-creation side effects).
    """
    _inherit = 'sale.order.line'

    rmc_project_id = fields.Many2one(
        'project.project', string='RMC Project',
        help="Construction project this concrete order line is committed against.")
    rmc_phase_id = fields.Many2one(
        'project.task', string='RMC Phase',
        domain="[('project_id', '=', rmc_project_id)]",
        help="Optional default phase for call-offs raised against this line.")
    rmc_free_unloading_minutes = fields.Integer(
        string='Free Unloading Minutes (Fallback)',
        help="Used only when no project rate card specifies a free unloading allowance. "
             "See free-unloading-minutes precedence in the README.")
    rmc_standby_rate_per_hour = fields.Monetary(
        string='Standby Rate/Hour (Fallback)', currency_field='currency_id',
        help="Used only when no project rate card specifies a standby rate.")
    rmc_delivered_qty_m3 = fields.Float(
        string='RMC Delivered (m³)', compute='_compute_rmc_delivered_qty_m3', digits='Product Unit',
        help="Sum of actual delivered m³ across all RMC loads against this line.")
    rmc_load_count = fields.Integer(string='RMC Load Count', compute='_compute_rmc_delivered_qty_m3')

    def _compute_rmc_delivered_qty_m3(self):
        load_data = self.env['rmc.load']._read_group(
            [('sale_order_line_id', 'in', self.ids), ('state', 'not in', ('cancelled', 'draft'))],
            ['sale_order_line_id'],
            ['actual_delivered_qty_m3:sum', '__count'],
        )
        stats = {line.id: (qty, count) for line, qty, count in load_data}
        for line in self:
            qty, count = stats.get(line.id, (0.0, 0))
            line.rmc_delivered_qty_m3 = qty
            line.rmc_load_count = count
