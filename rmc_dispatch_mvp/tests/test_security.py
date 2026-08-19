from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import RmcTestCommon


@tagged('post_install', '-at_install')
class TestSecurity(RmcTestCommon):

    def test_11_dispatcher_cannot_override_credit_driver_reads_own_loads_only(self):
        dispatcher = self._create_user('Dispatcher Sec', 'rmc_dispatcher_sec', ['rmc_dispatch_mvp.group_rmc_dispatcher'])
        driver_user = self._create_user('Driver Sec', 'rmc_driver_sec', ['rmc_dispatch_mvp.group_rmc_driver'])

        calloff = self._create_calloff(qty=20, phase=self.phase_foundation)
        calloff.action_confirm()
        calloff.credit_status = 'blocked'

        # Denied at wizard creation (no ir.model.access row for Dispatcher on this transient
        # model) - an equally valid, earlier enforcement point than the in-method group check
        # in RmcCreditOverrideWizard.action_confirm(), which is exercised directly below.
        with self.assertRaises(AccessError):
            self.env['rmc.credit.override.wizard'].with_user(dispatcher).create({
                'calloff_id': calloff.id, 'reason': 'Trying to bypass as dispatcher.',
            })
        wizard = self.env['rmc.credit.override.wizard'].create({
            'calloff_id': calloff.id, 'reason': 'Trying to bypass as dispatcher.',
        })
        with self.assertRaises(AccessError):
            wizard.with_user(dispatcher).action_confirm()

        calloff.credit_status = 'approved'
        calloff.action_create_loads()
        loads = calloff.load_ids.sorted('id')
        load_for_ali = loads[0]
        load_for_ali.driver_id = self.driver.id

        other_driver = self.env['res.partner'].create({'name': 'Other Driver'})
        load_for_other = loads[1]
        load_for_other.driver_id = other_driver.id

        driver_user.partner_id = self.driver

        visible = self.env['rmc.load'].with_user(driver_user).search([('id', 'in', loads.ids)])
        self.assertEqual(visible, load_for_ali)
        self.assertNotIn(load_for_other, visible)

    def test_22_rate_card_access_matrix(self):
        dispatcher = self._create_user('Dispatcher Rate', 'rmc_dispatcher_rate', ['rmc_dispatch_mvp.group_rmc_dispatcher'])
        batch_operator = self._create_user('Batch Operator Rate', 'rmc_batch_rate', ['rmc_dispatch_mvp.group_rmc_batch_operator'])

        rate_as_dispatcher = self.env['rmc.project.rate'].with_user(dispatcher).browse(self.rate_project.id)
        rate_as_dispatcher.rate_per_m3  # read access must succeed
        with self.assertRaises(AccessError):
            rate_as_dispatcher.write({'rate_per_m3': 999})

        with self.assertRaises(AccessError):
            self.env['rmc.project.rate'].with_user(batch_operator).search([('id', '=', self.rate_project.id)])
