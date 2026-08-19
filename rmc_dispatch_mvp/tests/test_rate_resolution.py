from datetime import datetime, timedelta

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import RmcTestCommon


@tagged('post_install', '-at_install')
class TestRateResolution(RmcTestCommon):

    def test_16_phase_card_beats_project_card(self):
        """ Phase-level rate card takes precedence over the project-level one. """
        calloff_foundation = self._create_calloff(phase=self.phase_foundation)
        self.assertEqual(calloff_foundation.agreed_rate_per_m3, 290)
        self.assertEqual(calloff_foundation.rate_source, 'project_rate_card')

        calloff_columns = self._create_calloff(phase=self.phase_columns)
        self.assertEqual(calloff_columns.agreed_rate_per_m3, 280)

    def test_17_date_effectivity(self):
        """ An expired card (date_to in the past) is not selected; an open-ended card is. """
        yesterday = datetime.now().date() - timedelta(days=1)
        two_years_ago = yesterday.replace(year=yesterday.year - 2)
        expired = self.env['rmc.project.rate'].create({
            'project_id': self.project.id, 'phase_id': self.phase_columns.id,
            'product_id': self.g40.id, 'rate_per_m3': 999,
            'date_from': two_years_ago, 'date_to': yesterday,
        })
        # Columns has no other phase-level card and the expired one must be ignored, so
        # resolution must fall through to the open-ended project-level card (280).
        calloff = self._create_calloff(phase=self.phase_columns)
        self.assertEqual(calloff.agreed_rate_per_m3, 280)
        self.assertNotEqual(calloff.project_rate_id, expired)

    def test_18_overlap_constraint(self):
        """ Overlapping active cards for the same project+phase+product are rejected; a
        project-level card does not conflict with a phase-level one (different scope). """
        with self.assertRaises(ValidationError):
            self.env['rmc.project.rate'].create({
                'project_id': self.project.id, 'phase_id': self.phase_foundation.id,
                'product_id': self.g40.id, 'rate_per_m3': 300,
                'date_from': self.rate_foundation.date_from,
            })
        # No exception expected: project-level (no phase) alongside phase-level for a
        # DIFFERENT phase must not conflict.
        self.env['rmc.project.rate'].create({
            'project_id': self.project.id, 'phase_id': self.phase_columns.id,
            'product_id': self.g40.id, 'rate_per_m3': 275,
            'date_from': self.rate_project.date_from,
        })

    def test_19_price_freeze(self):
        """ Confirming a call-off freezes the price; editing the rate card afterwards must not
        change the call-off, its loads, or the resulting invoice. """
        calloff = self._create_calloff(phase=self.phase_foundation)
        calloff.action_check_credit()
        calloff.action_confirm()
        self.assertEqual(calloff.agreed_rate_per_m3, 290)
        calloff.action_create_loads()
        load = calloff.load_ids[0]
        self.assertEqual(load.agreed_rate_per_m3, 290)

        self.rate_foundation.rate_per_m3 = 320

        load = self.env['rmc.load'].browse(load.id)
        calloff = self.env['rmc.calloff'].browse(calloff.id)
        self.assertEqual(calloff.agreed_rate_per_m3, 290)
        self.assertEqual(load.agreed_rate_per_m3, 290)

        self._drive_load_to_delivered(load)
        load.action_create_invoice()
        invoice_line = load.invoice_id.invoice_line_ids.filtered(lambda l: l.product_id == self.g40)
        self.assertEqual(invoice_line.price_unit, 290)

    def test_20_no_rate_card_fallback_disabled_by_default(self):
        """ With no matching rate card and the fallback setting off (default), confirming
        raises UserError. With the fallback enabled, it succeeds using the SO line price. """
        new_project = self.env['project.project'].create({'name': 'No Rate Project', 'partner_id': self.customer.id})
        phase = self.env['project.task'].create({'name': 'Phase', 'project_id': new_project.id})
        calloff = self.env['rmc.calloff'].create({
            'customer_id': self.customer.id, 'sale_order_id': self.sale_order.id,
            'sale_order_line_id': self.sale_order_line.id, 'project_id': new_project.id,
            'phase_id': phase.id, 'product_id': self.g40.id, 'requested_qty': 5,
            'requested_delivery_datetime': datetime.now(),
        })
        self.assertFalse(calloff.agreed_rate_per_m3)
        with self.assertRaises(UserError):
            calloff.action_confirm()

        self.company.rmc_allow_so_rate_fallback = True
        calloff._resolve_rate()
        calloff.action_confirm()
        self.assertEqual(calloff.rate_source, 'sale_order_line')
        self.assertEqual(calloff.agreed_rate_per_m3, self.sale_order_line.price_unit)

    def test_21_rate_card_deletion_blocked_once_referenced(self):
        """ A rate card referenced by a call-off cannot be deleted (ondelete=restrict) but can
        be archived. """
        calloff = self._create_calloff(phase=self.phase_foundation)
        self.assertEqual(calloff.project_rate_id, self.rate_foundation)
        with self.assertRaises(Exception):
            self.rate_foundation.unlink()
        self.rate_foundation.active = False
        self.assertFalse(self.rate_foundation.active)
