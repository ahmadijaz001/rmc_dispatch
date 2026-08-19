from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class RmcProjectRate(models.Model):
    """ Project rate card - the commercial source of truth for concrete price.

    See README "Design decisions and assumptions": price authority sits here, quantity
    authority sits on the sale order. The two are deliberately not collapsed into one model.
    """
    _name = 'rmc.project.rate'
    _description = 'RMC Project Rate Card'
    _inherit = ['mail.thread']
    _order = 'project_id, phase_id, product_id, date_from desc'

    company_id = fields.Many2one(
        'res.company', string='Company', required=True, default=lambda self: self.env.company)
    project_id = fields.Many2one(
        'project.project', string='Project', required=True, index=True, ondelete='restrict',
        help="Construction project this rate applies to.")
    phase_id = fields.Many2one(
        'project.task', string='Phase', domain="[('project_id', '=', project_id)]",
        help="Optional phase-level override. Leave empty for a project-wide rate.")
    customer_id = fields.Many2one(
        'res.partner', string='Customer', related='project_id.partner_id', store=True,
        index=True, help="Customer of the project, shown for reference and search.")
    product_id = fields.Many2one(
        'product.product', string='Concrete Grade', required=True,
        domain="[('is_rmc_concrete', '=', True)]", help="Concrete grade this rate prices.")
    currency_id = fields.Many2one(
        'res.currency', string='Currency', required=True,
        default=lambda self: self.env.company.currency_id)
    rate_per_m3 = fields.Monetary(
        string='Rate per m³', required=True, currency_field='currency_id', tracking=True,
        help="Agreed price per m³ of concrete for this project/phase/grade.")
    free_unloading_minutes = fields.Integer(
        string='Free Unloading Minutes',
        help="Free unloading allowance in minutes. 0 means 'not set at this level' - the "
             "precedence in the README falls through to the next level.")
    standby_rate_per_hour = fields.Monetary(
        string='Standby Rate/Hour', currency_field='currency_id',
        help="Standby charge per hour beyond the free unloading allowance. 0 means 'not set "
             "at this level' - the precedence in the README falls through to the next level.")
    date_from = fields.Date(string='Effective From', required=True, default=fields.Date.context_today)
    date_to = fields.Date(string='Effective To', help="Leave empty for an open-ended rate.")
    sale_order_id = fields.Many2one(
        'sale.order', string='Approving Sale Order',
        help="Sale order that commercially authorises this rate, for traceability.")
    sale_order_line_id = fields.Many2one(
        'sale.order.line', string='Approving Sale Order Line',
        domain="[('order_id', '=', sale_order_id)]")
    active = fields.Boolean(default=True)
    notes = fields.Text()
    load_count = fields.Integer(
        string='Loads Priced', compute='_compute_load_count',
        help="Number of confirmed loads frozen against this rate card.")

    @api.depends('project_id.name', 'phase_id.name', 'product_id.display_name', 'date_from')
    def _compute_display_name(self):
        for rate in self:
            parts = [rate.project_id.name]
            if rate.phase_id:
                parts.append(rate.phase_id.name)
            parts.append(rate.product_id.display_name)
            if rate.date_from:
                parts.append(_("from %s", rate.date_from))
            rate.display_name = ' / '.join(p for p in parts if p)

    def _compute_load_count(self):
        load_data = self.env['rmc.load']._read_group(
            [('project_rate_id', 'in', self.ids)], ['project_rate_id'], ['__count'])
        counts = {rate.id: count for rate, count in load_data}
        for rate in self:
            rate.load_count = counts.get(rate.id, 0)

    @api.constrains('phase_id', 'project_id')
    def _check_phase_belongs_to_project(self):
        for rate in self:
            if rate.phase_id and rate.phase_id.project_id != rate.project_id:
                raise ValidationError(_(
                    "The phase %(phase)s does not belong to project %(project)s.",
                    phase=rate.phase_id.display_name, project=rate.project_id.display_name))

    @api.constrains('product_id')
    def _check_product_is_rmc_concrete(self):
        for rate in self:
            if not rate.product_id.is_rmc_concrete:
                raise ValidationError(_(
                    "%(product)s is not flagged as an RMC Concrete product; it cannot have a "
                    "project rate card.", product=rate.product_id.display_name))

    @api.constrains('rate_per_m3')
    def _check_rate_positive(self):
        for rate in self:
            if rate.rate_per_m3 <= 0:
                raise ValidationError(_("The rate per m³ must be strictly positive."))

    @api.constrains('date_from', 'date_to')
    def _check_date_range(self):
        for rate in self:
            if rate.date_to and rate.date_to < rate.date_from:
                raise ValidationError(_("The effective-to date cannot be before the effective-from date."))

    @api.constrains('currency_id', 'company_id')
    def _check_currency_matches_company(self):
        for rate in self:
            if rate.currency_id != rate.company_id.currency_id:
                raise ValidationError(_(
                    "Multi-currency rate cards are not supported in this MVP. The rate "
                    "currency must be the company currency (%(currency)s).",
                    currency=rate.company_id.currency_id.name))

    @api.constrains('sale_order_id', 'customer_id')
    def _check_sale_order_partner(self):
        for rate in self:
            if rate.sale_order_id and rate.sale_order_id.partner_id != rate.customer_id:
                raise ValidationError(_(
                    "The approving sale order's customer does not match the project's customer."))

    @api.constrains('project_id', 'phase_id', 'product_id', 'date_from', 'date_to', 'active', 'company_id')
    def _check_no_overlap(self):
        for rate in self:
            if not rate.active:
                continue
            domain = [
                ('id', '!=', rate.id),
                ('project_id', '=', rate.project_id.id),
                ('phase_id', '=', rate.phase_id.id),
                ('product_id', '=', rate.product_id.id),
                ('company_id', '=', rate.company_id.id),
                ('active', '=', True),
            ]
            for other in self.env['rmc.project.rate'].search(domain):
                overlaps = (
                    not (other.date_to and other.date_to < rate.date_from)
                    and not (rate.date_to and rate.date_to < other.date_from)
                )
                if overlaps:
                    raise ValidationError(_(
                        "This rate card overlaps with another active rate card for the same "
                        "project, phase and concrete grade (%(other)s, %(other_from)s to "
                        "%(other_to)s). Archive or adjust the dates of one of them.",
                        other=other.display_name, other_from=other.date_from,
                        other_to=other.date_to or _('open-ended'),
                    ))

    @api.model
    def _rmc_get_applicable_rate(self, project, phase, product, date, company):
        """ Resolve the applicable rate for (project, phase, product) on ``date``.

        Returns a dict: {'rate_card': recordset_or_empty, 'rate_per_m3': float,
        'free_unloading_minutes': int, 'standby_rate_per_hour': float,
        'rate_source': 'project_rate_card'|'sale_order_line'}.

        Search order (see README "free-unloading-minutes precedence" and the rate-resolution
        docstring in the spec this module was built from):
          1. active card for project + phase + product, valid on date
          2. active card for project + product with no phase, valid on date
          3. controlled fallback to a confirmed sale order line (gated by
             company.rmc_allow_so_rate_fallback, default off)
          4. otherwise raise UserError
        """
        base_domain = [
            ('company_id', '=', company.id),
            ('project_id', '=', project.id),
            ('product_id', '=', product.id),
            ('active', '=', True),
            ('date_from', '<=', date),
            '|', ('date_to', '=', False), ('date_to', '>=', date),
        ]
        rate_card = self.env['rmc.project.rate']
        if phase:
            rate_card = self.search(base_domain + [('phase_id', '=', phase.id)], order='date_from desc', limit=1)
        if not rate_card:
            rate_card = self.search(base_domain + [('phase_id', '=', False)], order='date_from desc', limit=1)

        if rate_card:
            return {
                'rate_card': rate_card,
                'rate_per_m3': rate_card.rate_per_m3,
                'free_unloading_minutes': rate_card.free_unloading_minutes,
                'standby_rate_per_hour': rate_card.standby_rate_per_hour,
                'rate_source': 'project_rate_card',
            }

        if company.rmc_allow_so_rate_fallback:
            sol = self.env['sale.order.line'].search([
                ('order_id.partner_id', '=', project.partner_id.id),
                ('product_id', '=', product.id),
                ('order_id.state', '=', 'sale'),
                ('company_id', '=', company.id),
            ], order='id desc', limit=1)
            if sol:
                return {
                    'rate_card': self.env['rmc.project.rate'],
                    'rate_per_m3': sol.price_unit,
                    'free_unloading_minutes': sol.rmc_free_unloading_minutes,
                    'standby_rate_per_hour': sol.rmc_standby_rate_per_hour,
                    'rate_source': 'sale_order_line',
                }

        raise UserError(_(
            "No active project rate card exists for this project, phase and concrete grade. "
            "Ask the RMC Manager to create one before dispatching."))
