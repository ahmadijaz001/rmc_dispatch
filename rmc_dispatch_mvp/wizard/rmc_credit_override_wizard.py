from odoo import _, fields, models
from odoo.exceptions import AccessError


class RmcCreditOverrideWizard(models.TransientModel):
    """ Captures a mandatory reason before an RMC Manager overrides a credit block.
    Enforced in Python (not just hidden in the view) per README security notes: hiding a
    button is not access control. """
    _name = 'rmc.credit.override.wizard'
    _description = 'RMC Credit Override'

    calloff_id = fields.Many2one('rmc.calloff', required=True)
    reason = fields.Text(required=True)

    def action_confirm(self):
        self.ensure_one()
        if not self.env.user.has_group('rmc_dispatch_mvp.group_rmc_manager'):
            raise AccessError(_("Only an RMC Manager can override a credit block."))
        self.calloff_id.write({
            'credit_status': 'overridden',
            'credit_override_user_id': self.env.user.id,
            'credit_override_reason': self.reason,
        })
        self.calloff_id.message_post(body=_(
            "Credit block overridden by %(user)s: %(reason)s",
            user=self.env.user.display_name, reason=self.reason))
