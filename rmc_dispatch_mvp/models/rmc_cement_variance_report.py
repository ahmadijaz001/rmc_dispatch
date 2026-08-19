from odoo import fields, models, tools


class RmcCementVarianceReport(models.Model):
    """ _auto=False analysis view. Reads directly from rmc_load's own stored cement-variance
    columns (design/actual kg per m³ - see rmc_load.py _compute_cement_variance, which already
    joins mrp_production -> stock_move -> product_product/product_template filtered to
    rmc_material_type='cement') rather than repeating that join a second time in raw SQL.
    Deviates from the spec's literal join-chain wording for this report; see NOTES.md. """
    _name = 'rmc.cement.variance.report'
    _description = 'RMC Cement Variance'
    _auto = False
    _order = 'date desc'

    load_id = fields.Many2one('rmc.load', string='Load', readonly=True)
    date = fields.Datetime(string='Scheduled Departure', readonly=True)
    company_id = fields.Many2one('res.company', readonly=True)
    project_id = fields.Many2one('project.project', readonly=True)
    product_id = fields.Many2one('product.product', string='Grade', readonly=True)
    plant_id = fields.Many2one('stock.warehouse', string='Plant', readonly=True)
    batched_qty_m3 = fields.Float(string='Batched (m³)', readonly=True)
    design_cement_kg_per_m3 = fields.Float(string='Design Cement (kg/m³)', readonly=True)
    actual_cement_kg_per_m3 = fields.Float(string='Actual Cement (kg/m³)', readonly=True)
    variance_kg_per_m3 = fields.Float(string='Variance (kg/m³)', readonly=True)
    variance_pct = fields.Float(string='Variance %', readonly=True)

    def search_fetch(self, domain, field_names=None, offset=0, limit=None, order=None):
        # This view selects rmc_load's stored compute columns directly (see class docstring);
        # those computes may still be pending in this transaction, so force a flush first or a
        # load updated moments ago could show stale figures here.
        self.env['rmc.load'].flush_model(
            ['design_cement_kg_per_m3', 'actual_cement_kg_per_m3', 'cement_variance_kg_per_m3'])
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
                    l.plant_id AS plant_id,
                    l.actual_batched_qty_m3 AS batched_qty_m3,
                    COALESCE(l.design_cement_kg_per_m3, 0.0) AS design_cement_kg_per_m3,
                    COALESCE(l.actual_cement_kg_per_m3, 0.0) AS actual_cement_kg_per_m3,
                    COALESCE(l.cement_variance_kg_per_m3, 0.0) AS variance_kg_per_m3,
                    CASE WHEN COALESCE(l.design_cement_kg_per_m3, 0.0) = 0 THEN 0.0
                         ELSE COALESCE(l.cement_variance_kg_per_m3, 0.0) / l.design_cement_kg_per_m3 * 100.0
                    END AS variance_pct
                FROM rmc_load l
                JOIN rmc_calloff c ON c.id = l.calloff_id
                WHERE l.state NOT IN ('cancelled', 'draft', 'scheduled') AND l.manufacturing_order_id IS NOT NULL
            )
        """ % self._table)
