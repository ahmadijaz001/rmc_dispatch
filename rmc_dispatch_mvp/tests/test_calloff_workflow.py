from datetime import datetime

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import RmcTestCommon


@tagged('post_install', '-at_install')
class TestCalloffWorkflow(RmcTestCommon):

    def test_08_qty_override_requires_manager(self):
        """ A call-off exceeding the remaining contract quantity is blocked for a dispatcher
        and allowed for a manager who supplies an override reason. """
        dispatcher = self._create_user('Dispatcher', 'rmc_dispatcher_qty', ['rmc_dispatch_mvp.group_rmc_dispatcher'])
        manager = self._create_user('Manager', 'rmc_manager_qty', ['rmc_dispatch_mvp.group_rmc_manager'])

        calloff = self._create_calloff(qty=150, phase=self.phase_foundation)  # SO only has 120 m3

        with self.assertRaises(UserError):
            calloff.with_user(dispatcher).action_confirm()

        with self.assertRaises(UserError):
            # manager but no reason yet
            calloff.with_user(manager).action_confirm()

        calloff.qty_override_reason = 'Customer verbally confirmed extra pour on site.'
        calloff.with_user(manager).action_confirm()
        self.assertEqual(calloff.state, 'confirmed')

    def test_09_credit_block_and_override(self):
        """ Credit over limit blocks load creation; an RMC Manager override permits it and
        records the acting user. """
        # credit_limit/credit alone don't deterministically produce a receivable in a unit
        # test without posting real invoices, so the blocked state itself is set directly -
        # what's under test here is action_create_loads()'s guard and the override flow, not
        # the credit computation (covered separately via action_check_credit()'s company-
        # setting branch in test_reports/test_security).
        calloff = self._create_calloff(phase=self.phase_foundation)
        calloff.action_confirm()
        calloff.credit_status = 'blocked'

        with self.assertRaises(UserError):
            calloff.action_create_loads()

        manager = self._create_user('Manager Override', 'rmc_manager_override', ['rmc_dispatch_mvp.group_rmc_manager'])
        wizard = self.env['rmc.credit.override.wizard'].with_user(manager).create({
            'calloff_id': calloff.id, 'reason': 'Customer paid outstanding balance by wire, awaiting reconciliation.',
        })
        wizard.action_confirm()
        self.assertEqual(calloff.credit_status, 'overridden')
        self.assertEqual(calloff.credit_override_user_id, manager)
        calloff.action_create_loads()
        self.assertTrue(calloff.load_ids)
