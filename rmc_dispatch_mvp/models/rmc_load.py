from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.float_utils import float_compare, float_is_zero

# Default estimated round-trip time used to seed fleet.vehicle.next_available_datetime on
# dispatch. There is no telematics/GPS integration in the MVP (see README "Known MVP
# limitations"), so this is a fixed planning assumption, not a measurement; dispatchers can
# freely edit next_available_datetime afterwards.
DEFAULT_CYCLE_TIME_MINUTES = 120

# Field-level write restrictions for groups that only get ir.model.access write=1 on rmc.load
# for a narrow purpose (record rules and ir.model.access are all-or-nothing per model, so this
# is enforced here in Python rather than only hidden in the view - see README security notes).
DRIVER_WRITABLE_FIELDS = {
    'state', 'actual_delivered_qty_m3', 'returned_qty_m3', 'returned_reason_id', 'variance_reason',
    'signed_by', 'delivery_signature', 'water_added_on_site', 'water_added_litres',
    'delivery_notes', 'unloading_start', 'unloading_end', 'site_arrival', 'return_to_plant_datetime',
}
BATCH_OPERATOR_WRITABLE_FIELDS = {
    'state', 'actual_batched_qty_m3', 'variance_reason', 'actual_batch_start', 'actual_batch_end',
    'qc_sample_id', 'fleet_vehicle_id', 'driver_id', 'plant_id', 'scheduled_departure', 'planned_qty_m3',
}


class RmcLoad(models.Model):
    """ One truck load = one delivery ticket. The operational source of truth: every other
    document (MO, invoice, QC sample) links to this record. See README for the full
    architecture note on why this is deliberate. """
    _name = 'rmc.load'
    _description = 'RMC Load'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'scheduled_departure desc, id desc'

    name = fields.Char(default='New', copy=False, readonly=True, help="Load / delivery ticket number.")
    calloff_id = fields.Many2one(
        'rmc.calloff', string='Call-off', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one('res.company', related='calloff_id.company_id', store=True, readonly=True)
    customer_id = fields.Many2one('res.partner', related='calloff_id.customer_id', store=True, readonly=True)
    sale_order_id = fields.Many2one('sale.order', related='calloff_id.sale_order_id', store=True, readonly=True)
    sale_order_line_id = fields.Many2one('sale.order.line', related='calloff_id.sale_order_line_id', store=True, readonly=True)
    project_id = fields.Many2one('project.project', related='calloff_id.project_id', store=True, readonly=True)
    phase_id = fields.Many2one('project.task', related='calloff_id.phase_id', store=True, readonly=True)
    product_id = fields.Many2one('product.product', related='calloff_id.product_id', store=True, readonly=True)
    currency_id = fields.Many2one('res.currency', related='calloff_id.currency_id', store=True, readonly=True)
    project_rate_id = fields.Many2one(
        'rmc.project.rate', related='calloff_id.project_rate_id', store=True, readonly=True,
        groups='rmc_dispatch_mvp.group_rmc_finance,rmc_dispatch_mvp.group_rmc_dispatcher,rmc_dispatch_mvp.group_rmc_manager')
    agreed_rate_per_m3 = fields.Monetary(
        related='calloff_id.agreed_rate_per_m3', store=True, readonly=True, currency_field='currency_id',
        groups='rmc_dispatch_mvp.group_rmc_finance,rmc_dispatch_mvp.group_rmc_dispatcher,rmc_dispatch_mvp.group_rmc_manager',
        help="Frozen price, read from the call-off. Hidden from Drivers, Batch Operators and QC Technicians.")

    planned_qty_m3 = fields.Float(string='Planned Quantity (m³)', digits='Product Unit', required=True)
    actual_batched_qty_m3 = fields.Float(string='Actual Batched (m³)', digits='Product Unit')
    actual_delivered_qty_m3 = fields.Float(string='Actual Delivered (m³)', digits='Product Unit')
    returned_qty_m3 = fields.Float(string='Returned (m³)', digits='Product Unit')
    variance_qty_m3 = fields.Float(
        string='Variance (m³)', compute='_compute_variance_qty_m3', store=True, digits='Product Unit',
        help="Batched minus delivered minus returned. Should be ~0; a large variance means "
             "concrete was neither delivered nor recorded as returned.")
    variance_reason = fields.Text(help="Required when the variance exceeds the configured tolerance.")
    returned_reason_id = fields.Many2one('rmc.return.reason', string='Return Reason')

    fleet_vehicle_id = fields.Many2one(
        'fleet.vehicle', string='Truck', domain="[('ready_for_dispatch', '=', True)]")
    driver_id = fields.Many2one('res.partner', string='Driver')
    plant_id = fields.Many2one('stock.warehouse', string='Batching Plant', required=True,
                                default=lambda self: self.env['stock.warehouse'].search([], limit=1))

    scheduled_departure = fields.Datetime()
    actual_batch_start = fields.Datetime(readonly=True)
    actual_batch_end = fields.Datetime(readonly=True)
    actual_departure = fields.Datetime(readonly=True)
    site_arrival = fields.Datetime(readonly=True)
    unloading_start = fields.Datetime()
    unloading_end = fields.Datetime()
    return_to_plant_datetime = fields.Datetime()

    cycle_time_minutes = fields.Float(string='Cycle Time (min)', compute='_compute_time_metrics', store=True)
    unloading_minutes = fields.Float(string='Unloading Time (min)', compute='_compute_time_metrics', store=True)
    free_unloading_minutes = fields.Integer(
        related='calloff_id.free_unloading_minutes', store=True, readonly=True)
    standby_minutes = fields.Float(string='Standby (min)', compute='_compute_time_metrics', store=True)
    standby_rate_per_hour = fields.Monetary(
        related='calloff_id.standby_rate_per_hour', store=True, readonly=True, currency_field='currency_id')
    standby_charge = fields.Monetary(
        string='Standby Charge', compute='_compute_time_metrics', store=True, currency_field='currency_id',
        groups='rmc_dispatch_mvp.group_rmc_finance,rmc_dispatch_mvp.group_rmc_dispatcher,rmc_dispatch_mvp.group_rmc_manager')

    signed_by = fields.Char(string='Signed By')
    delivery_signature = fields.Binary(string='Signature')
    water_added_on_site = fields.Boolean(string='Water Added On Site')
    water_added_litres = fields.Float(string='Water Added (L)')
    delivery_notes = fields.Text()

    manufacturing_order_id = fields.Many2one('mrp.production', string='Manufacturing Order', readonly=True, copy=False)
    picking_id = fields.Many2one(
        'stock.picking', string='Delivery Picking', copy=False,
        help="Placeholder for a future phase. Not used in this MVP: concrete is non-storable "
             "so no stock.picking is created - rmc.load itself is the delivery document. "
             "See README §3.1.")
    invoice_id = fields.Many2one('account.move', string='Invoice', readonly=True, copy=False)
    invoice_line_ids = fields.One2many('account.move.line', 'rmc_load_id', string='Invoice Lines')
    qc_sample_id = fields.Many2one('rmc.qc.sample', string='QC Sample', readonly=True, copy=False)

    material_cost = fields.Monetary(
        string='Material Cost', compute='_compute_costing', store=True, currency_field='currency_id',
        groups='rmc_dispatch_mvp.group_rmc_finance,rmc_dispatch_mvp.group_rmc_manager')
    sales_value = fields.Monetary(
        string='Sales Value', compute='_compute_costing', store=True, currency_field='currency_id',
        groups='rmc_dispatch_mvp.group_rmc_finance,rmc_dispatch_mvp.group_rmc_manager')
    gross_margin = fields.Monetary(
        string='Gross Margin', compute='_compute_costing', store=True, currency_field='currency_id',
        groups='rmc_dispatch_mvp.group_rmc_finance,rmc_dispatch_mvp.group_rmc_manager')
    margin_pct = fields.Float(
        string='Margin %', compute='_compute_costing', store=True,
        groups='rmc_dispatch_mvp.group_rmc_finance,rmc_dispatch_mvp.group_rmc_manager')

    actual_cement_kg = fields.Float(string='Actual Cement (kg)', compute='_compute_cement_variance', store=True)
    actual_cement_kg_per_m3 = fields.Float(
        string='Actual Cement (kg/m³)', compute='_compute_cement_variance', store=True)
    design_cement_kg_per_m3 = fields.Float(
        string='Design Cement (kg/m³)', related='manufacturing_order_id.bom_id.design_cement_kg_per_m3', store=True)
    cement_variance_kg_per_m3 = fields.Float(
        string='Cement Variance (kg/m³)', compute='_compute_cement_variance', store=True)

    credit_warning = fields.Char(string='Credit Warning', compute='_compute_credit_warning', store=True)

    state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('scheduled', 'Scheduled'),
            ('batching', 'Batching'),
            ('batched', 'Batched'),
            ('in_transit', 'In Transit'),
            ('delivered', 'Delivered'),
            ('invoiced', 'Invoiced'),
            ('cancelled', 'Cancelled'),
        ],
        default='draft', required=True, tracking=True, copy=False)

    _manufacturing_order_unique = models.Constraint(
        "UNIQUE (manufacturing_order_id)",
        "Each manufacturing order can only be linked to one load "
        "(NULLs are unaffected, so loads without an MO yet are unrestricted).",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('rmc.load') or 'New'
        return super().create(vals_list)

    def write(self, vals):
        self._check_group_field_restrictions(vals)
        return super().write(vals)

    def _check_group_field_restrictions(self, vals):
        """ ir.model.access and record rules are all-or-nothing per model; the Driver and
        Batch Operator groups are granted model-level write=1 on rmc.load for the narrow
        purpose described in the security matrix (README), so the actual field scope is
        enforced here in Python - not left to the view alone. """
        user = self.env.user
        if user._is_admin() or user.has_group('rmc_dispatch_mvp.group_rmc_manager') \
                or user.has_group('rmc_dispatch_mvp.group_rmc_dispatcher') \
                or user.has_group('rmc_dispatch_mvp.group_rmc_finance'):
            return
        allowed = None
        if user.has_group('rmc_dispatch_mvp.group_rmc_driver'):
            allowed = DRIVER_WRITABLE_FIELDS
        elif user.has_group('rmc_dispatch_mvp.group_rmc_batch_operator'):
            allowed = BATCH_OPERATOR_WRITABLE_FIELDS
        if allowed is None:
            raise AccessError(_("You are not allowed to modify RMC loads."))
        forbidden = set(vals) - allowed
        if forbidden:
            raise AccessError(_(
                "Your role is not allowed to modify the following field(s) on an RMC load: "
                "%(fields)s", fields=', '.join(sorted(forbidden))))

    @api.depends('actual_batched_qty_m3', 'actual_delivered_qty_m3', 'returned_qty_m3')
    def _compute_variance_qty_m3(self):
        for load in self:
            load.variance_qty_m3 = load.actual_batched_qty_m3 - load.actual_delivered_qty_m3 - load.returned_qty_m3

    @api.depends('actual_departure', 'return_to_plant_datetime', 'unloading_start', 'unloading_end',
                 'free_unloading_minutes', 'standby_rate_per_hour')
    def _compute_time_metrics(self):
        for load in self:
            load.cycle_time_minutes = load._minutes_between(load.actual_departure, load.return_to_plant_datetime)
            load.unloading_minutes = load._minutes_between(load.unloading_start, load.unloading_end)
            standby_minutes = max(0.0, load.unloading_minutes - (load.free_unloading_minutes or 0))
            load.standby_minutes = standby_minutes if load.unloading_minutes else 0.0
            load.standby_charge = (load.standby_minutes / 60.0) * (load.standby_rate_per_hour or 0.0)

    @staticmethod
    def _minutes_between(start, end):
        if not (start and end) or end <= start:
            return 0.0
        return (end - start).total_seconds() / 60.0

    @api.depends('manufacturing_order_id.move_raw_ids.value', 'manufacturing_order_id.move_raw_ids.state',
                 'actual_delivered_qty_m3', 'agreed_rate_per_m3', 'standby_charge')
    def _compute_costing(self):
        for load in self:
            raw_moves = load.manufacturing_order_id.move_raw_ids.filtered(lambda m: m.state == 'done')
            load.material_cost = sum(raw_moves.mapped('value'))
            load.sales_value = load.actual_delivered_qty_m3 * load.agreed_rate_per_m3 + load.standby_charge
            load.gross_margin = load.sales_value - load.material_cost
            load.margin_pct = (load.gross_margin / load.sales_value * 100.0) if load.sales_value else 0.0

    @api.depends('manufacturing_order_id.move_raw_ids.quantity', 'manufacturing_order_id.move_raw_ids.state',
                 'actual_batched_qty_m3', 'design_cement_kg_per_m3')
    def _compute_cement_variance(self):
        kg_uom = self.env.ref('uom.product_uom_kgm', raise_if_not_found=False)
        for load in self:
            cement_moves = load.manufacturing_order_id.move_raw_ids.filtered(
                lambda m: m.state == 'done' and m.product_id.rmc_material_type == 'cement')
            cement_kg = 0.0
            if kg_uom:
                for move in cement_moves:
                    cement_kg += move.product_uom._compute_quantity(move.quantity, kg_uom, raise_if_failure=False)
            load.actual_cement_kg = cement_kg
            load.actual_cement_kg_per_m3 = cement_kg / load.actual_batched_qty_m3 if load.actual_batched_qty_m3 else 0.0
            load.cement_variance_kg_per_m3 = load.actual_cement_kg_per_m3 - (load.design_cement_kg_per_m3 or 0.0)

    @api.depends('calloff_id.credit_status')
    def _compute_credit_warning(self):
        for load in self:
            if load.calloff_id.credit_status == 'blocked':
                load.credit_warning = _('Customer over credit limit')
            elif load.calloff_id.credit_status == 'overridden':
                load.credit_warning = _('Credit override in effect')
            else:
                load.credit_warning = False

    @api.constrains('returned_qty_m3', 'actual_batched_qty_m3')
    def _check_returned_qty(self):
        for load in self:
            if float_compare(load.returned_qty_m3, 0.0, precision_digits=2) < 0:
                raise ValidationError(_("Returned quantity cannot be negative."))
            if float_compare(load.returned_qty_m3, load.actual_batched_qty_m3, precision_digits=2) > 0:
                raise ValidationError(_(
                    "Returned quantity (%(returned).2f m³) cannot exceed actual batched "
                    "quantity (%(batched).2f m³).",
                    returned=load.returned_qty_m3, batched=load.actual_batched_qty_m3))

    @api.constrains('actual_delivered_qty_m3')
    def _check_delivered_qty(self):
        for load in self:
            if float_compare(load.actual_delivered_qty_m3, 0.0, precision_digits=2) < 0:
                raise ValidationError(_("Delivered quantity cannot be negative."))

    @api.constrains('returned_qty_m3', 'returned_reason_id')
    def _check_returned_reason(self):
        for load in self:
            if float_compare(load.returned_qty_m3, 0.0, precision_digits=2) > 0 and not load.returned_reason_id:
                raise ValidationError(_("A return reason is required when returned quantity is greater than zero."))

    @api.constrains('variance_qty_m3', 'variance_reason', 'state')
    def _check_variance_reason(self):
        # Delivered/returned quantities aren't meaningful until the load reaches 'delivered'
        # (they stay 0 through batching/dispatch), so checking the batched-vs-delivered+returned
        # reconciliation any earlier would flag every load as a false-positive variance the
        # moment it's marked batched, before delivery data even exists.
        tolerance = self.env.company.rmc_variance_tolerance_m3 or 0.05
        for load in self.filtered(lambda l: l.state in ('delivered', 'invoiced')):
            if abs(load.variance_qty_m3) > tolerance and not load.variance_reason:
                raise ValidationError(_(
                    "The batching variance (%(variance).3f m³) exceeds the tolerance "
                    "(%(tolerance).3f m³). Enter a variance reason.",
                    variance=load.variance_qty_m3, tolerance=tolerance))

    @api.constrains('planned_qty_m3', 'calloff_id')
    def _check_sibling_planned_qty(self):
        for load in self:
            siblings = load.calloff_id.load_ids.filtered(lambda l: l.state != 'cancelled')
            total = sum(siblings.mapped('planned_qty_m3'))
            if float_compare(total, load.calloff_id.requested_qty, precision_digits=2) > 0:
                if not self.env.user.has_group('rmc_dispatch_mvp.group_rmc_manager'):
                    raise ValidationError(_(
                        "The total planned quantity of loads (%(total).2f m³) exceeds the "
                        "call-off's requested quantity (%(requested).2f m³). Only an RMC "
                        "Manager can create loads beyond the requested quantity.",
                        total=total, requested=load.calloff_id.requested_qty))

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    def _check_state(self, expected, method_name):
        wrong = self.filtered(lambda l: l.state not in expected)
        if wrong:
            raise UserError(_(
                "%(method)s cannot be used on load %(load)s in state '%(state)s'.",
                method=method_name, load=wrong[0].display_name, state=wrong[0].state))

    def action_confirm(self):
        self._check_state(['draft'], 'Confirm')
        for load in self:
            if not (load.fleet_vehicle_id and load.driver_id and load.plant_id):
                raise UserError(_("Truck, driver and batching plant are required to confirm a load."))
            if float_compare(load.planned_qty_m3, 0.0, precision_digits=2) <= 0:
                raise UserError(_("Planned quantity must be strictly positive."))
            load.state = 'scheduled'
            load.message_post(body=_("Load confirmed and scheduled."))

    def action_create_mo(self):
        self._check_state(['scheduled'], 'Create Manufacturing Order')
        for load in self:
            if load.manufacturing_order_id:
                raise UserError(_("This load already has a manufacturing order."))
            bom_date = (load.scheduled_departure or fields.Datetime.now()).date()
            bom = self.env['mrp.bom']._rmc_find_bom(load.product_id, bom_date)
            if not bom:
                raise UserError(_(
                    "No Bill of Material found for %(product)s effective on %(date)s.",
                    product=load.product_id.display_name, date=bom_date))
            # sudo: RMC groups do not carry base MRP app permissions (out of scope for this
            # module's security matrix - see README); the manufacturing order is an
            # implementation detail of the load lifecycle, driven entirely through rmc.load's
            # own guarded state machine, not exposed for free-form editing by RMC users.
            production = self.env['mrp.production'].sudo().create({
                'product_id': load.product_id.id,
                'product_qty': load.planned_qty_m3,
                'product_uom_id': load.product_id.uom_id.id,
                'bom_id': bom.id,
                'company_id': load.calloff_id.company_id.id,
                'origin': load.name,
            })
            production.action_confirm()
            load.manufacturing_order_id = production.id
            load.message_post(body=_("Manufacturing order %(mo)s created.", mo=production.display_name))

    def action_start_batching(self):
        self._check_state(['scheduled'], 'Start Batching')
        self.write({'state': 'batching', 'actual_batch_start': fields.Datetime.now()})

    def action_mark_batched(self):
        self._check_state(['batching'], 'Mark Batched')
        for load in self:
            if float_compare(load.actual_batched_qty_m3, 0.0, precision_digits=2) <= 0:
                raise UserError(_("Enter the actual batched quantity before marking this load as batched."))
            if not load.manufacturing_order_id:
                raise UserError(_("This load has no manufacturing order to complete."))
            # sudo: see action_create_mo() - RMC groups do not carry base MRP permissions.
            production = load.manufacturing_order_id.sudo()
            production.qty_producing = load.actual_batched_qty_m3
            production._set_qty_producing()
            # skip_consumption: aggregate/sand quantities are corrected for moisture on every
            # load and will differ from the BoM - see README "BoM consumption='flexible'
            # requirement". skip_backorder: actual_batched_qty_m3 (qty_producing) can differ
            # from planned_qty_m3 (product_qty), which would otherwise raise a second,
            # independent wizard (mrp.production.backorder) - found by reading
            # pre_button_mark_done(), not mentioned in the original spec. See NOTES.md.
            result = production.with_context(skip_consumption=True, skip_backorder=True).button_mark_done()
            if result is not True:
                raise UserError(_(
                    "The manufacturing order could not be completed automatically (got an "
                    "unexpected wizard action). Check that the BoM's Flexible Consumption is "
                    "set to 'Allowed'."))
            load.write({'state': 'batched', 'actual_batch_end': fields.Datetime.now()})
            load._create_qc_sample()
            load.message_post(body=_("Batching completed at %(qty).2f m³.", qty=load.actual_batched_qty_m3))

    def _create_qc_sample(self):
        self.ensure_one()
        if self.qc_sample_id:
            return self.qc_sample_id
        # sudo: the Batch Operator group does not have create access on rmc.qc.sample (only
        # QC Technician and Manager do, per the security matrix); auto-creating the sample on
        # batching completion is a system side-effect, not the operator managing QC records.
        sample = self.env['rmc.qc.sample'].sudo().create({'load_id': self.id})
        self.qc_sample_id = sample.id
        return sample

    def action_dispatch(self):
        self._check_state(['batched'], 'Dispatch')
        for load in self:
            now = fields.Datetime.now()
            load.write({'state': 'in_transit', 'actual_departure': now})
            if load.fleet_vehicle_id:
                load.fleet_vehicle_id.write({
                    'current_load_id': load.id,
                    'next_available_datetime': now + timedelta(minutes=DEFAULT_CYCLE_TIME_MINUTES),
                })
            load.message_post(body=_("Load dispatched."))

    def action_mark_arrived(self):
        self._check_state(['in_transit'], 'Mark Arrived')
        self.write({'site_arrival': fields.Datetime.now()})

    def action_confirm_delivery(self):
        self._check_state(['in_transit'], 'Confirm Delivery')
        for load in self:
            if not load.signed_by:
                raise UserError(_("The delivery must be signed before it can be confirmed."))
            if not (load.unloading_start and load.unloading_end):
                raise UserError(_("Unloading start and end times are required to confirm delivery."))
            if load.water_added_on_site and float_is_zero(load.water_added_litres, precision_digits=2):
                raise UserError(_("Enter the litres of water added on site."))
            load.write({'state': 'delivered'})
            if load.fleet_vehicle_id and load.fleet_vehicle_id.current_load_id == load:
                # sudo: drivers confirming their own delivery do not have write access to
                # fleet.vehicle; clearing the truck's current load is a system side-effect of
                # delivery, not a manual vehicle edit.
                load.fleet_vehicle_id.sudo().current_load_id = False
            load._sync_sale_order_line_delivered_qty()
            load.message_post(body=_(
                "Delivery confirmed: %(delivered).2f m³ delivered, %(returned).2f m³ returned.",
                delivered=load.actual_delivered_qty_m3, returned=load.returned_qty_m3))

    def _sync_sale_order_line_delivered_qty(self):
        """ Write qty_delivered back to the sale order line as the sum of ACTUAL delivered m³
        across all non-cancelled loads on that line (not planned, not just this load). See
        README/NOTES.md for why this write persists even though qty_delivered_method computes
        to 'stock_move' rather than 'manual' for this non-storable product. """
        lines = self.mapped('sale_order_line_id')
        for line in lines:
            loads = self.env['rmc.load'].search([
                ('sale_order_line_id', '=', line.id), ('state', 'not in', ('draft', 'cancelled')),
            ])
            line.qty_delivered = sum(loads.mapped('actual_delivered_qty_m3'))

    def action_record_return(self):
        self._check_state(['delivered'], 'Record Return')
        # returned_qty_m3 / returned_reason_id are expected to already be written on the load
        # (from the form) before calling this button; the @api.constrains above re-validate.
        for load in self:
            load.message_post(body=_(
                "Return recorded: %(qty).2f m³, reason: %(reason)s.",
                qty=load.returned_qty_m3, reason=load.returned_reason_id.name or ''))

    def action_create_invoice(self):
        self._check_state(['delivered'], 'Create Invoice')
        for load in self:
            if load.invoice_id:
                raise UserError(_("This load has already been invoiced."))
            chargeable_return = load.returned_reason_id.chargeable
            if float_is_zero(load.actual_delivered_qty_m3, precision_digits=2) and not chargeable_return:
                raise UserError(_(
                    "Nothing was delivered on this load and the return reason is not "
                    "chargeable - there is nothing to invoice."))
            line_vals = []
            sol = load.sale_order_line_id
            if not float_is_zero(load.actual_delivered_qty_m3, precision_digits=2):
                line_vals.append((0, 0, {
                    'product_id': load.product_id.id,
                    'name': _("%(product)s - %(load)s", product=load.product_id.display_name, load=load.name),
                    'quantity': load.actual_delivered_qty_m3,
                    'price_unit': load.agreed_rate_per_m3,
                    'product_uom_id': load.product_id.uom_id.id,
                    'tax_ids': [(6, 0, sol.tax_ids.ids)],
                    'sale_line_ids': [(4, sol.id)],
                    'analytic_distribution': load._get_analytic_distribution(),
                }))
            if float_compare(load.standby_minutes, 0.0, precision_digits=2) > 0:
                standby_product = load.calloff_id.company_id.rmc_standby_service_product_id
                if not standby_product:
                    raise UserError(_(
                        "A standby charge applies to this load but no Standby Service Product "
                        "is configured in Settings."))
                line_vals.append((0, 0, {
                    'product_id': standby_product.id,
                    'name': _("Standby charge - %(load)s", load=load.name),
                    'quantity': load.standby_minutes / 60.0,
                    'price_unit': load.standby_rate_per_hour,
                    'analytic_distribution': load._get_analytic_distribution(),
                }))
            if chargeable_return:
                charge_product = load.returned_reason_id.default_charge_product_id
                line_vals.append((0, 0, {
                    'product_id': charge_product.id,
                    'name': _("Return charge (%(reason)s) - %(load)s",
                              reason=load.returned_reason_id.name, load=load.name),
                    'quantity': 1,
                    'price_unit': charge_product.lst_price,
                    'analytic_distribution': load._get_analytic_distribution(),
                }))
            invoice = self.env['account.move'].create({
                'move_type': 'out_invoice',
                'partner_id': load.customer_id.id,
                'currency_id': load.currency_id.id,
                'invoice_origin': load.sale_order_id.name,
                'invoice_line_ids': line_vals,
            })
            invoice.line_ids.write({'rmc_load_id': load.id})
            load.write({'invoice_id': invoice.id, 'state': 'invoiced'})
            load.message_post(body=_("Draft invoice %(invoice)s created.", invoice=invoice.name or invoice.display_name))

    def _get_analytic_distribution(self):
        self.ensure_one()
        analytic_account = self.project_id.account_id
        if analytic_account:
            return {str(analytic_account.id): 100.0}
        return False

    def action_cancel(self):
        if not (self.env.user._is_admin() or self.env.user.has_group('rmc_dispatch_mvp.group_rmc_manager')):
            raise AccessError(_("Only an RMC Manager can cancel a load."))
        for load in self:
            if load.state == 'invoiced':
                raise UserError(_("An invoiced load cannot be cancelled."))
            if load.manufacturing_order_id and load.manufacturing_order_id.state not in ('done', 'cancel'):
                load.manufacturing_order_id.sudo().action_cancel()  # sudo: see action_create_mo()
            if load.fleet_vehicle_id and load.fleet_vehicle_id.current_load_id == load:
                load.fleet_vehicle_id.sudo().current_load_id = False  # sudo: see action_confirm_delivery()
            load.state = 'cancelled'
            load.message_post(body=_("Load cancelled."))

    def action_open_mo(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'mrp.production',
            'view_mode': 'form',
            'res_id': self.manufacturing_order_id.id,
        }

    def action_open_invoice(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': self.invoice_id.id,
        }

    def action_open_qc(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'rmc.qc.sample',
            'view_mode': 'form',
            'res_id': self.qc_sample_id.id,
        }
