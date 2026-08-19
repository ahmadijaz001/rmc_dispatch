from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    """ Extension: expose RMC dispatch defaults (stored on res.company) in Settings. """
    _inherit = 'res.config.settings'

    rmc_default_free_unloading_minutes = fields.Integer(
        related='company_id.rmc_default_free_unloading_minutes', readonly=False)
    rmc_default_standby_rate_per_hour = fields.Monetary(
        related='company_id.rmc_default_standby_rate_per_hour', readonly=False)
    rmc_standby_service_product_id = fields.Many2one(
        related='company_id.rmc_standby_service_product_id', readonly=False)
    rmc_default_load_size_m3 = fields.Float(
        related='company_id.rmc_default_load_size_m3', readonly=False)
    rmc_variance_tolerance_m3 = fields.Float(
        related='company_id.rmc_variance_tolerance_m3', readonly=False)
    rmc_credit_check_blocks_dispatch = fields.Boolean(
        related='company_id.rmc_credit_check_blocks_dispatch', readonly=False)
    rmc_allow_so_rate_fallback = fields.Boolean(
        related='company_id.rmc_allow_so_rate_fallback', readonly=False)
