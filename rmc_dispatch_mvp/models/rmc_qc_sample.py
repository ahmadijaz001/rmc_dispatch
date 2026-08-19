from datetime import timedelta

from odoo import _, api, fields, models


class RmcQcSample(models.Model):
    """ Quality-control cube sample, auto-created once per load on batching completion.
    Traceability works by navigation: sample -> load -> MO -> BoM -> raw stock.move lines
    (see the smart buttons below and on rmc.load / mrp.production). """
    _name = 'rmc.qc.sample'
    _description = 'RMC QC Sample'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'sample_date desc, id desc'

    name = fields.Char(default='New', copy=False, readonly=True)
    load_id = fields.Many2one('rmc.load', string='Load', required=True, ondelete='cascade', index=True)
    product_id = fields.Many2one('product.product', related='load_id.product_id', store=True, readonly=True)
    project_id = fields.Many2one('project.project', related='load_id.project_id', store=True, readonly=True)
    customer_id = fields.Many2one('res.partner', related='load_id.customer_id', store=True, readonly=True)
    manufacturing_order_id = fields.Many2one(
        'mrp.production', related='load_id.manufacturing_order_id', store=True, readonly=True)
    bom_id = fields.Many2one('mrp.bom', related='manufacturing_order_id.bom_id', store=True, readonly=True)
    company_id = fields.Many2one(
        'res.company', related='load_id.calloff_id.company_id', store=True, readonly=True)

    sample_date = fields.Date(default=fields.Date.context_today)
    slump_mm = fields.Float(string='Slump (mm)')
    concrete_temperature_c = fields.Float(string='Concrete Temperature (°C)')
    ambient_temperature_c = fields.Float(string='Ambient Temperature (°C)')
    target_strength = fields.Float(string='Target Strength (MPa)')
    approval_reference = fields.Char(string='Mix Approval Reference (DCL)')

    test_7_day_date = fields.Date(string='7-Day Test Date', compute='_compute_test_dates', store=True)
    test_7_day_result = fields.Float(string='7-Day Result (MPa)')
    test_28_day_date = fields.Date(string='28-Day Test Date', compute='_compute_test_dates', store=True)
    test_28_day_result = fields.Float(string='28-Day Result (MPa)')

    cube_1_result = fields.Float(string='Cube 1 (MPa)')
    cube_2_result = fields.Float(string='Cube 2 (MPa)')
    cube_3_result = fields.Float(string='Cube 3 (MPa)')
    average_strength = fields.Float(string='Average Strength (MPa)', compute='_compute_average_strength', store=True)
    strength_ratio = fields.Float(string='Strength Ratio', compute='_compute_average_strength', store=True,
                                   help="Average strength divided by target strength.")

    state = fields.Selection(
        [('pending', 'Pending'), ('passed', 'Passed'), ('failed', 'Failed')],
        default='pending', required=True, tracking=True)
    notes = fields.Text()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('rmc.qc.sample') or 'New'
        samples = super().create(vals_list)
        for sample in samples:
            if not sample.target_strength:
                sample.target_strength = sample.product_id.target_strength or sample.bom_id.target_strength
            if not sample.approval_reference:
                sample.approval_reference = (
                    sample.product_id.mix_approval_reference or sample.bom_id.approval_reference)
            if sample.test_28_day_date:
                sample.activity_schedule(
                    'mail.mail_activity_data_todo',
                    date_deadline=sample.test_28_day_date,
                    summary=_("28-day strength test due for %(sample)s", sample=sample.name),
                )
        return samples

    @api.depends('sample_date')
    def _compute_test_dates(self):
        for sample in self:
            if sample.sample_date:
                sample.test_7_day_date = sample.sample_date + timedelta(days=7)
                sample.test_28_day_date = sample.sample_date + timedelta(days=28)
            else:
                sample.test_7_day_date = False
                sample.test_28_day_date = False

    @api.depends('cube_1_result', 'cube_2_result', 'cube_3_result', 'target_strength')
    def _compute_average_strength(self):
        for sample in self:
            results = [r for r in (sample.cube_1_result, sample.cube_2_result, sample.cube_3_result) if r]
            sample.average_strength = sum(results) / len(results) if results else 0.0
            sample.strength_ratio = (sample.average_strength / sample.target_strength) if sample.target_strength else 0.0
            if sample.average_strength:
                sample.state = 'passed' if sample.strength_ratio >= 1.0 else 'failed'
            else:
                sample.state = 'pending'

    def action_open_load(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'rmc.load', 'view_mode': 'form', 'res_id': self.load_id.id}

    def action_open_mo(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'mrp.production', 'view_mode': 'form',
                'res_id': self.manufacturing_order_id.id}

    def action_open_bom(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'mrp.bom', 'view_mode': 'form', 'res_id': self.bom_id.id}

    def action_open_raw_moves(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'stock.move',
            'view_mode': 'list,form',
            'domain': [('raw_material_production_id', '=', self.manufacturing_order_id.id)],
        }
