from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ProductTemplate(models.Model):
    """ Extension: flag ready-mix concrete products and raw material roles.

    Concrete products are never storable (see README "Design decisions and assumptions" -
    §3.1): they are produced and delivered the same day, and rmc.load is the delivery
    document, not a stock.picking.
    """
    _inherit = 'product.template'

    is_rmc_concrete = fields.Boolean(
        string='Is RMC Concrete',
        help="Check this for a sellable ready-mix concrete grade (e.g. G40). "
             "Concrete products must be non-storable 'Goods' priced and delivered per m³.")
    concrete_grade = fields.Char(
        string='Concrete Grade', help="Commercial grade code shown to customers, e.g. G40.")
    target_strength = fields.Float(
        string='Target Strength (MPa)',
        help="Design compressive strength in megapascals, used as the QC pass/fail reference.")
    default_free_unloading_minutes = fields.Integer(
        string='Default Free Unloading Minutes',
        help="Fallback free unloading allowance used when neither the project rate card nor "
             "the sale order line specifies one. See free-unloading-minutes precedence in the README.")
    mix_approval_reference = fields.Char(
        string='Mix Approval Reference (DCL)',
        help="Reference of the Dubai Central Laboratory (or equivalent) mix design approval.")
    rmc_material_type = fields.Selection(
        [
            ('cement', 'Cement'),
            ('scm', 'SCM / GGBS / Microsilica'),
            ('aggregate', 'Aggregate'),
            ('sand', 'Sand'),
            ('water', 'Water'),
            ('admixture', 'Admixture'),
            ('other', 'Other'),
        ],
        string='RMC Material Type',
        help="Role of this raw material in a concrete mix design. Required on cement so the "
             "cement variance report can identify which BoM lines are cement.")

    @api.constrains('is_rmc_concrete', 'type', 'is_storable', 'uom_id')
    def _check_rmc_concrete_setup(self):
        cubic_meter = self.env.ref('uom.product_uom_cubic_meter', raise_if_not_found=False)
        for product in self:
            if not product.is_rmc_concrete:
                continue
            if product.type != 'consu':
                raise ValidationError(_(
                    "%(product)s is flagged as RMC Concrete but its Product Type is not "
                    "'Goods'. Concrete must be type='consu' (it is manufactured and delivered "
                    "the same day, never stocked) — change the Product Type to 'Goods'.",
                    product=product.display_name,
                ))
            if product.is_storable:
                raise ValidationError(_(
                    "%(product)s is flagged as RMC Concrete but 'Track Inventory' is enabled. "
                    "Concrete must not be storable, otherwise Odoo will try to value stock that "
                    "does not physically exist once it leaves the plant — disable 'Track "
                    "Inventory' on this product.",
                    product=product.display_name,
                ))
            if cubic_meter and product.uom_id != cubic_meter:
                raise ValidationError(_(
                    "%(product)s is flagged as RMC Concrete but its Unit of Measure is not m³. "
                    "Set the Unit of Measure to m³ (activate uom.product_uom_cubic_meter if it "
                    "is archived).",
                    product=product.display_name,
                ))
