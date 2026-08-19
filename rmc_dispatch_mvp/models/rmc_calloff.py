from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.float_utils import float_compare, float_is_zero


class RmcCalloff(models.Model):
    """ A customer's daily concrete request, split into truck loads on confirmation.

    Price/allowance resolution and freezing: see rmc.project.rate._rmc_get_applicable_rate()
    and README "Design decisions and assumptions". agreed_rate_per_m3, rate_source and
    free_unloading_minutes are plain stored fields (NOT `compute=` fields) populated by
    _resolve_rate(); this is a deliberate deviation from the spec's field table (which
    labelled them "compute") because a real Odoo compute field re-evaluates whenever its
    declared dependencies change - including a rate card edited after confirmation - which
    would break the price-freeze requirement verified by test_19_price_freeze. See NOTES.md.
    """
    _name = 'rmc.calloff'
    _description = 'RMC Call-off'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'requested_delivery_datetime desc, id desc'

    name = fields.Char(default='New', copy=False, readonly=True, help="Call-off reference.")
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    customer_id = fields.Many2one(
        'res.partner', string='Customer', required=True, domain="[('is_company', '=', True)]")
    sale_order_id = fields.Many2one(
        'sale.order', string='Sale Order', required=True,
        domain="[('partner_id', '=', customer_id), ('state', '=', 'sale')]")
    sale_order_line_id = fields.Many2one(
        'sale.order.line', string='Sale Order Line', required=True,
        domain="[('order_id', '=', sale_order_id), ('product_id.is_rmc_concrete', '=', True)]")
    project_id = fields.Many2one('project.project', string='Project', required=True)
    phase_id = fields.Many2one(
        'project.task', string='Phase', required=True,
        domain="[('project_id', '=', project_id)]")
    product_id = fields.Many2one(
        'product.product', string='Concrete Grade', required=True,
        domain="[('is_rmc_concrete', '=', True)]")
    uom_id = fields.Many2one('uom.uom', string='Unit of Measure', related='product_id.uom_id', readonly=True)
    requested_qty = fields.Float(
        string='Requested Quantity (m³)', digits='Product Unit', required=True,
        help="Concrete volume requested for this call-off, in m³.")
    requested_delivery_datetime = fields.Datetime(
        string='Requested Delivery', required=True,
        help="Date/time used (in the company timezone) to resolve the applicable rate card.")

    ordered_qty = fields.Float(
        string='Contracted Quantity (m³)', compute='_compute_contract_quantities', store=True,
        digits='Product Unit', help="Quantity committed on the sale order line.")
    previously_called_qty = fields.Float(
        string='Previously Called (m³)', compute='_compute_contract_quantities', store=True,
        digits='Product Unit',
        help="Sum of requested quantity on other confirmed call-offs against the same sale order line.")
    remaining_contract_qty = fields.Float(
        string='Remaining Contract (m³)', compute='_compute_contract_quantities', store=True,
        digits='Product Unit', help="Contracted quantity minus previously called quantity.")

    project_rate_id = fields.Many2one(
        'rmc.project.rate', string='Project Rate Card', ondelete='restrict', copy=False,
        help="Rate card the price was resolved from. Empty when the sale-order fallback was used.")
    rate_source = fields.Selection(
        [('project_rate_card', 'Project Rate Card'), ('sale_order_line', 'Sale Order Line (fallback)')],
        string='Rate Source', copy=False, tracking=True,
        help="Where the frozen price came from. 'Sale Order Line' means the project rate "
             "card fallback setting was used - see README.")
    agreed_rate_per_m3 = fields.Monetary(
        string='Agreed Rate/m³', currency_field='currency_id', copy=False, tracking=True,
        help="Price per m³ frozen at confirmation. Never re-resolved afterwards, even if the "
             "source rate card is later edited.")
    currency_id = fields.Many2one('res.currency', related='sale_order_id.currency_id', string='Currency', readonly=True)
    free_unloading_minutes = fields.Integer(
        string='Free Unloading Minutes', copy=False,
        help="Frozen at confirmation from the precedence in the README "
             "(rate card > sale order line > product default > company setting > 45).")
    standby_rate_per_hour = fields.Monetary(
        string='Standby Rate/Hour', currency_field='currency_id', copy=False,
        help="Frozen at confirmation, same precedence as free unloading minutes.")

    state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('confirmed', 'Confirmed'),
            ('partially_dispatched', 'Partially Dispatched'),
            ('dispatched', 'Dispatched'),
            ('cancelled', 'Cancelled'),
        ],
        default='draft', required=True, tracking=True, copy=False)
    credit_status = fields.Selection(
        [
            ('not_checked', 'Not Checked'),
            ('approved', 'Approved'),
            ('blocked', 'Blocked'),
            ('overridden', 'Overridden'),
        ],
        default='not_checked', required=True, tracking=True, copy=False)
    credit_override_user_id = fields.Many2one('res.users', readonly=True, copy=False)
    credit_override_reason = fields.Text(copy=False)
    qty_override_reason = fields.Text(
        help="Required to confirm a call-off that exceeds the remaining contract quantity; "
             "only an RMC Manager can supply this.")

    load_ids = fields.One2many('rmc.load', 'calloff_id', string='Loads')
    load_count = fields.Integer(compute='_compute_load_stats')
    dispatched_qty = fields.Float(
        string='Dispatched Quantity (m³)', compute='_compute_load_stats', digits='Product Unit')
    notes = fields.Html()

    @api.depends('sale_order_line_id.product_uom_qty', 'sale_order_line_id', 'requested_qty', 'state')
    def _compute_contract_quantities(self):
        for calloff in self:
            calloff.ordered_qty = calloff.sale_order_line_id.product_uom_qty
            others = self.search([
                ('sale_order_line_id', '=', calloff.sale_order_line_id.id),
                ('state', 'in', ('confirmed', 'partially_dispatched', 'dispatched')),
                ('id', '!=', calloff.id or 0),
            ]) if calloff.sale_order_line_id else self.browse()
            calloff.previously_called_qty = sum(others.mapped('requested_qty'))
            calloff.remaining_contract_qty = calloff.ordered_qty - calloff.previously_called_qty

    def _compute_load_stats(self):
        load_data = self.env['rmc.load']._read_group(
            [('calloff_id', 'in', self.ids), ('state', '!=', 'cancelled')],
            ['calloff_id'], ['planned_qty_m3:sum', '__count'])
        stats = {calloff.id: (qty, count) for calloff, qty, count in load_data}
        for calloff in self:
            qty, count = stats.get(calloff.id, (0.0, 0))
            calloff.dispatched_qty = qty
            calloff.load_count = count

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('rmc.calloff') or 'New'
        calloffs = super().create(vals_list)
        calloffs._resolve_rate()
        return calloffs

    def write(self, vals):
        frozen_fields = {'project_rate_id', 'rate_source', 'agreed_rate_per_m3', 'free_unloading_minutes', 'standby_rate_per_hour'}
        if frozen_fields.intersection(vals) and any(calloff.state != 'draft' for calloff in self):
            if not self.env.context.get('rmc_allow_frozen_write'):
                raise UserError(_("The agreed rate is frozen once a call-off is confirmed and cannot be edited."))
        res = super().write(vals)
        resolve_triggers = {'project_id', 'phase_id', 'product_id', 'requested_delivery_datetime'}
        if resolve_triggers.intersection(vals):
            self.filtered(lambda c: c.state == 'draft')._resolve_rate()
        return res

    def _resolve_rate(self, raise_if_not_found=False):
        """ Resolve and (re)write the rate snapshot for draft call-offs. Called from create(),
        from write() on project/phase/product/date changes while draft (raise_if_not_found=
        False: a draft call-off is allowed to sit unpriced until someone creates a rate card or
        a manager fixes the project/phase), and once more from action_confirm() with
        raise_if_not_found=True, which is where "no rate card exists" must actually surface to
        the user. Never called afterwards. """
        for calloff in self.filtered(lambda c: c.state == 'draft'):
            if not (calloff.project_id and calloff.phase_id and calloff.product_id and calloff.requested_delivery_datetime):
                continue
            company = calloff.company_id
            tz_date = fields.Datetime.context_timestamp(
                calloff.with_context(tz=company.partner_id.tz), calloff.requested_delivery_datetime).date()
            try:
                result = self.env['rmc.project.rate']._rmc_get_applicable_rate(
                    calloff.project_id, calloff.phase_id, calloff.product_id, tz_date, company)
            except UserError:
                if raise_if_not_found:
                    raise
                continue
            free_minutes = result['free_unloading_minutes']
            if not free_minutes:
                free_minutes = calloff.sale_order_line_id.rmc_free_unloading_minutes
            if not free_minutes:
                free_minutes = calloff.product_id.default_free_unloading_minutes
            if not free_minutes:
                free_minutes = company.rmc_default_free_unloading_minutes
            if not free_minutes:
                free_minutes = 45
            standby_rate = result['standby_rate_per_hour']
            if not standby_rate:
                standby_rate = calloff.sale_order_line_id.rmc_standby_rate_per_hour
            if not standby_rate:
                standby_rate = company.rmc_default_standby_rate_per_hour
            calloff.with_context(rmc_allow_frozen_write=True).write({
                'project_rate_id': result['rate_card'].id or False,
                'rate_source': result['rate_source'],
                'agreed_rate_per_m3': result['rate_per_m3'],
                'free_unloading_minutes': free_minutes,
                'standby_rate_per_hour': standby_rate,
            })
            if result['rate_source'] == 'sale_order_line':
                calloff.message_post(body=_(
                    "%(user)s confirmed pricing using the sale order line fallback rate "
                    "(no matching project rate card found).", user=self.env.user.display_name))

    @api.constrains('product_id')
    def _check_product_is_rmc_concrete(self):
        for calloff in self:
            if not calloff.product_id.is_rmc_concrete:
                raise ValidationError(_("%(product)s is not an RMC Concrete product.", product=calloff.product_id.display_name))

    @api.constrains('phase_id', 'project_id')
    def _check_phase_belongs_to_project(self):
        for calloff in self:
            if calloff.phase_id.project_id != calloff.project_id:
                raise ValidationError(_("The phase does not belong to the selected project."))

    @api.constrains('sale_order_id', 'customer_id')
    def _check_sale_order_partner(self):
        for calloff in self:
            if calloff.sale_order_id.partner_id != calloff.customer_id:
                raise ValidationError(_("The sale order's customer does not match the call-off customer."))

    @api.constrains('requested_qty')
    def _check_requested_qty_positive(self):
        for calloff in self:
            if float_compare(calloff.requested_qty, 0.0, precision_digits=2) <= 0:
                raise ValidationError(_("The requested quantity must be strictly positive."))

    def action_check_credit(self):
        for calloff in self:
            company = calloff.company_id
            if not company.account_use_credit_limit:
                calloff.credit_status = 'not_checked'
                calloff.message_post(body=_("Credit check skipped: Sales Credit Limit is disabled for this company."))
                continue
            # sudo: credit/credit_limit are restricted to accounting groups, but the credit
            # gate must apply regardless of the dispatcher's accounting access.
            partner = calloff.customer_id.sudo()
            if partner.credit_limit <= 0:
                calloff.credit_status = 'not_checked'
                continue
            if float_compare(partner.credit, partner.credit_limit, precision_digits=2) > 0:
                calloff.credit_status = 'blocked'
            else:
                calloff.credit_status = 'approved'

    def action_override_credit(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'rmc.credit.override.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_calloff_id': self.id},
        }

    def action_confirm(self):
        for calloff in self:
            if calloff.state != 'draft':
                raise UserError(_("Only a draft call-off can be confirmed."))
            is_manager = self.env.user.has_group('rmc_dispatch_mvp.group_rmc_manager')
            if float_compare(calloff.requested_qty, calloff.remaining_contract_qty, precision_digits=2) > 0:
                if not is_manager:
                    raise UserError(_(
                        "This call-off requests %(requested).2f m³ but only %(remaining).2f m³ "
                        "remains on the sale order line. Only an RMC Manager can override this.",
                        requested=calloff.requested_qty, remaining=calloff.remaining_contract_qty))
                if not calloff.qty_override_reason:
                    raise UserError(_(
                        "This call-off exceeds the remaining contract quantity. Enter a "
                        "quantity override reason before confirming."))
            calloff._resolve_rate(raise_if_not_found=True)
            if not calloff.agreed_rate_per_m3:
                # _resolve_rate(raise_if_not_found=True) already raises via
                # _rmc_get_applicable_rate() when nothing resolves; this is a defensive
                # backstop in case project/phase/product/date were incomplete (so _resolve_rate
                # skipped resolution entirely rather than raising).
                raise UserError(_(
                    "No active project rate card exists for this project, phase and concrete "
                    "grade. Ask the RMC Manager to create one before dispatching."))
            if calloff.credit_status == 'not_checked':
                calloff.action_check_credit()
            calloff.state = 'confirmed'
            calloff.message_post(body=_("Call-off confirmed at %(rate)s/m³ (source: %(source)s).",
                                         rate=calloff.agreed_rate_per_m3, source=calloff.rate_source))

    def action_create_loads(self):
        for calloff in self:
            if calloff.state not in ('confirmed', 'partially_dispatched'):
                raise UserError(_("Loads can only be created from a confirmed call-off."))
            if calloff.credit_status == 'blocked':
                raise UserError(_("This call-off's customer is over their credit limit. "
                                   "An RMC Manager must override the credit block before dispatching."))
            remaining = calloff.requested_qty - sum(calloff.load_ids.filtered(lambda l: l.state != 'cancelled').mapped('planned_qty_m3'))
            if float_compare(remaining, 0.0, precision_digits=2) <= 0:
                raise UserError(_("This call-off is already fully split into loads."))
            default_size = calloff.company_id.rmc_default_load_size_m3 or 8.0
            load_vals = []
            qty_left = remaining
            while float_compare(qty_left, 0.0, precision_digits=2) > 0:
                chunk = min(default_size, qty_left)
                qty_left -= chunk
                if float_is_zero(qty_left, precision_digits=2):
                    chunk += qty_left  # fold any negative rounding remainder into the last load
                    qty_left = 0.0
                load_vals.append({'calloff_id': calloff.id, 'planned_qty_m3': chunk})
            self.env['rmc.load'].create(load_vals)
            total_planned = sum(calloff.load_ids.filtered(lambda l: l.state != 'cancelled').mapped('planned_qty_m3'))
            calloff.state = 'dispatched' if float_compare(total_planned, calloff.requested_qty, precision_digits=2) >= 0 else 'partially_dispatched'

    def action_cancel(self):
        if not (self.env.user._is_admin() or self.env.user.has_group('rmc_dispatch_mvp.group_rmc_manager')):
            raise AccessError(_("Only an RMC Manager can cancel a call-off."))
        for calloff in self:
            if any(load.state not in ('draft', 'scheduled', 'cancelled') for load in calloff.load_ids):
                raise UserError(_("Cannot cancel a call-off that already has loads in progress or delivered."))
            calloff.load_ids.filtered(lambda l: l.state != 'cancelled').action_cancel()
            calloff.state = 'cancelled'
