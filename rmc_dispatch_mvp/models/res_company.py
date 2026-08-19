from odoo import fields, models


class ResCompany(models.Model):
    """ Extension: RMC dispatch defaults, editable from Settings (res_config_settings.py). """
    _inherit = 'res.company'

    rmc_default_free_unloading_minutes = fields.Integer(
        string='Default Free Unloading Minutes', default=45,
        help="Lowest-priority fallback used when no project rate card, sale order line or "
             "product specifies a free unloading allowance.")
    rmc_default_standby_rate_per_hour = fields.Monetary(
        string='Default Standby Rate/Hour', currency_field='currency_id',
        help="Lowest-priority fallback used when no project rate card or sale order line "
             "specifies a standby rate.")
    rmc_standby_service_product_id = fields.Many2one(
        'product.product', string='Standby Service Product',
        help="Service product used for the standby-time invoice line.")
    rmc_default_load_size_m3 = fields.Float(
        string='Default Load Size (m³)', default=8.0, digits='Product Unit',
        help="Used to split a call-off into loads when no vehicle capacity is chosen yet.")
    rmc_variance_tolerance_m3 = fields.Float(
        string='Batching Variance Tolerance (m³)', default=0.05, digits='Product Unit',
        help="A load's batched/delivered/returned variance below this tolerance does not "
             "require a variance reason.")
    rmc_credit_check_blocks_dispatch = fields.Boolean(
        string='Credit Check Blocks Dispatch', default=True,
        help="If enabled, a call-off whose customer is over their credit limit cannot create "
             "loads unless an RMC Manager overrides the credit block.")
    rmc_allow_so_rate_fallback = fields.Boolean(
        string='Allow Sale Order Rate Fallback', default=False,
        help="If enabled, confirming a call-off with no matching project rate card falls back "
             "to the confirmed sale order line's price instead of blocking. Disabled by "
             "default: see README 'why the sale-order rate fallback is disabled by default'.")
