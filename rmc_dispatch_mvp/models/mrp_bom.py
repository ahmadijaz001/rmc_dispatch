from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class MrpBom(models.Model):
    """ Extension: mix-design metadata and consumption-variance safety net.

    See README §"BoM consumption='flexible' requirement" - actual raw material consumption
    (especially aggregate/sand, corrected for moisture) differs from the BoM on every load,
    so any RMC concrete BoM must allow flexible consumption or button_mark_done() on the MO
    will always return a mrp.consumption.warning wizard action instead of completing.
    """
    _inherit = 'mrp.bom'

    approval_reference = fields.Char(
        string='Mix Approval Reference', tracking=True,
        help="Reference of the lab-approved mix design this BoM implements.")
    effective_date_from = fields.Date(
        string='Effective From', tracking=True,
        help="First date this mix design may be used to produce. Leave empty for always effective.")
    effective_date_to = fields.Date(
        string='Effective To', tracking=True,
        help="Last date this mix design may be used to produce. Leave empty for open-ended.")
    target_slump_mm = fields.Float(string='Target Slump (mm)')
    target_strength = fields.Float(string='Target Strength (MPa)')
    design_cement_kg_per_m3 = fields.Float(
        string='Design Cement (kg/m³)', compute='_compute_rmc_design_kg_per_m3',
        help="Cement quantity per m³ of concrete produced by this BoM, from the component "
             "lines flagged as RMC Material Type = Cement.")
    design_water_litre_per_m3 = fields.Float(
        string='Design Water (L/m³)', compute='_compute_rmc_design_kg_per_m3',
        help="Water quantity per m³ of concrete produced by this BoM, from the component "
             "lines flagged as RMC Material Type = Water.")

    @api.depends('bom_line_ids.product_qty', 'bom_line_ids.product_uom_id',
                 'bom_line_ids.product_id.rmc_material_type', 'product_qty')
    def _compute_rmc_design_kg_per_m3(self):
        kg_uom = self.env.ref('uom.product_uom_kgm', raise_if_not_found=False)
        litre_uom = self.env.ref('uom.product_uom_litre', raise_if_not_found=False)
        for bom in self:
            cement_kg = 0.0
            water_litre = 0.0
            for line in bom.bom_line_ids:
                material_type = line.product_id.rmc_material_type
                if material_type == 'cement' and kg_uom:
                    cement_kg += line.product_uom_id._compute_quantity(
                        line.product_qty, kg_uom, raise_if_failure=False)
                elif material_type == 'water' and litre_uom:
                    water_litre += line.product_uom_id._compute_quantity(
                        line.product_qty, litre_uom, raise_if_failure=False)
            divisor = bom.product_qty or 1.0
            bom.design_cement_kg_per_m3 = cement_kg / divisor
            bom.design_water_litre_per_m3 = water_litre / divisor

    @api.constrains('consumption', 'product_tmpl_id')
    def _check_rmc_consumption_flexible(self):
        for bom in self:
            if bom.product_tmpl_id.is_rmc_concrete and bom.consumption != 'flexible':
                raise ValidationError(_(
                    "The BoM for %(product)s produces RMC concrete. Its 'Flexible Consumption' "
                    "setting must be 'Allowed' (flexible), otherwise completing the "
                    "manufacturing order for every load will be blocked by a consumption "
                    "warning, because actual aggregate/sand consumption always differs from "
                    "the BoM after moisture correction.",
                    product=bom.product_tmpl_id.display_name,
                ))

    @api.model
    def _rmc_find_bom(self, product, date):
        """ Return the applicable BoM for ``product`` on ``date``, respecting mix-design
        effectivity dates. Falls back to the standard mrp.bom._bom_find() resolution (ignoring
        effectivity) when no BoM has effectivity dates configured, so the helper also works for
        non-RMC boms used in tests/demo without an effectivity window.
        """
        domain = [
            ('product_tmpl_id', '=', product.product_tmpl_id.id),
            ('type', '=', 'normal'),
            ('active', '=', True),
            '|', ('product_id', '=', product.id), ('product_id', '=', False),
            '|', ('effective_date_from', '=', False), ('effective_date_from', '<=', date),
            '|', ('effective_date_to', '=', False), ('effective_date_to', '>=', date),
        ]
        bom = self.search(domain, order='sequence, product_id, id', limit=1)
        if bom:
            return bom
        return self._bom_find(product).get(product, self.browse())
