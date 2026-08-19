from odoo import fields, models, tools


class RmcLoadMarginReport(models.Model):
    """ _auto=False analysis view. Reads directly from rmc_load's own stored costing columns
    (material_cost, sales_value, gross_margin, margin_pct - see rmc_load.py) rather than
    re-deriving the MO raw-move valuation join a second time in raw SQL, so the report can
    never drift from the figures shown on the load itself. Deviates from the spec's literal
    join-chain wording for this report; see NOTES.md. """
    _name = 'rmc.load.margin.report'
    _description = 'RMC Load Margin Analysis'
    _auto = False
    _order = 'date desc'

    load_id = fields.Many2one('rmc.load', string='Load', readonly=True)
    date = fields.Datetime(string='Scheduled Departure', readonly=True)
    company_id = fields.Many2one('res.company', readonly=True)
    project_id = fields.Many2one('project.project', readonly=True)
    product_id = fields.Many2one('product.product', string='Grade', readonly=True)
    customer_id = fields.Many2one('res.partner', string='Customer', readonly=True)
    plant_id = fields.Many2one('stock.warehouse', string='Plant', readonly=True)
    currency_id = fields.Many2one('res.currency', readonly=True)
    delivered_qty_m3 = fields.Float(string='Delivered (m³)', readonly=True)
    sales_value = fields.Monetary(string='Sales Value', currency_field='currency_id', readonly=True)
    standby_revenue = fields.Monetary(string='Standby Revenue', currency_field='currency_id', readonly=True)
    material_cost = fields.Monetary(string='Material Cost', currency_field='currency_id', readonly=True)
    gross_margin = fields.Monetary(string='Gross Margin', currency_field='currency_id', readonly=True)
    margin_pct = fields.Float(string='Margin %', readonly=True)

    def search_fetch(self, domain, field_names=None, offset=0, limit=None, order=None):
        # This view selects rmc_load's stored compute columns directly (see class docstring);
        # those computes may still be pending in this transaction, so force a flush first or a
        # load updated moments ago could show stale figures here.
        self.env['rmc.load'].flush_model(
            ['sales_value', 'standby_charge', 'material_cost', 'gross_margin', 'margin_pct'])
        return super().search_fetch(domain, field_names=field_names, offset=offset, limit=limit, order=order)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE VIEW %s AS (
                SELECT
                    l.id AS id,
                    l.id AS load_id,
                    l.scheduled_departure AS date,
                    c.company_id AS company_id,
                    l.project_id AS project_id,
                    l.product_id AS product_id,
                    l.customer_id AS customer_id,
                    l.plant_id AS plant_id,
                    l.currency_id AS currency_id,
                    l.actual_delivered_qty_m3 AS delivered_qty_m3,
                    COALESCE(l.sales_value, 0.0) - COALESCE(l.standby_charge, 0.0) AS sales_value,
                    COALESCE(l.standby_charge, 0.0) AS standby_revenue,
                    COALESCE(l.material_cost, 0.0) AS material_cost,
                    COALESCE(l.gross_margin, 0.0) AS gross_margin,
                    COALESCE(l.margin_pct, 0.0) AS margin_pct
                FROM rmc_load l
                JOIN rmc_calloff c ON c.id = l.calloff_id
                WHERE l.state != 'cancelled'
            )
        """ % self._table)
