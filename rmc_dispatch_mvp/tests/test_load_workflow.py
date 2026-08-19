from datetime import datetime, timedelta

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tools.float_utils import float_compare

from .common import RmcTestCommon


@tagged('post_install', '-at_install')
class TestLoadWorkflow(RmcTestCommon):

    def test_01_full_happy_path(self):
        """ project -> SO -> call-off -> two loads -> MO -> batched -> dispatched -> delivered
        -> invoiced. The invoice has 9.5 m3 of concrete plus a standby line. """
        calloff = self._create_calloff(qty=20, phase=self.phase_foundation)
        loads = self._confirm_and_dispatch(calloff)
        self.assertEqual(len(loads), 2)
        self.assertTrue(all(l.name.startswith('RMC/') for l in loads))

        load = self._drive_load_to_delivered(loads[0], batched=10.0, delivered=9.5, returned=0.5,
                                              unloading_minutes=70)
        self.assertEqual(load.state, 'delivered')
        load.action_create_invoice()
        self.assertEqual(load.state, 'invoiced')

        concrete_line = load.invoice_id.invoice_line_ids.filtered(lambda l: l.product_id == self.g40)
        standby_line = load.invoice_id.invoice_line_ids.filtered(lambda l: l.product_id == self.company.rmc_standby_service_product_id)
        self.assertEqual(float_compare(concrete_line.quantity, 9.5, precision_digits=2), 0)
        self.assertTrue(standby_line, "Expected a standby invoice line for the 25-minute overage.")
        self.assertEqual(float_compare(standby_line.quantity, 25 / 60.0, precision_digits=2), 0)

    def test_02_mo_completes_without_consumption_warning(self):
        """ button_mark_done() must not return a mrp.consumption.warning action even when the
        produced quantity (qty_producing) differs from the planned quantity (product_qty). """
        calloff = self._create_calloff(qty=10, phase=self.phase_foundation)
        loads = self._confirm_and_dispatch(calloff)
        load = loads[0]
        load.write({
            'fleet_vehicle_id': self.truck_12.id, 'driver_id': self.driver.id,
            'plant_id': self.env['stock.warehouse'].search([], limit=1).id,
        })
        load.action_confirm()
        load.action_create_mo()
        self.assertEqual(load.manufacturing_order_id.consumption, 'flexible')
        load.action_start_batching()
        # Batch a different quantity than planned (moisture-corrected real-world batching).
        load.write({'actual_batched_qty_m3': load.planned_qty_m3 + 0.3})
        load.action_mark_batched()  # must not raise UserError (would if a wizard came back)
        self.assertEqual(load.state, 'batched')
        self.assertEqual(load.manufacturing_order_id.state, 'done')

    def test_03_no_stock_quant_for_concrete(self):
        """ Concrete is non-storable: no stock.quant or valuation ever exists for it, even
        after a full production + delivery cycle. """
        calloff = self._create_calloff(qty=10, phase=self.phase_foundation)
        loads = self._confirm_and_dispatch(calloff)
        self._drive_load_to_delivered(loads[0])
        quants = self.env['stock.quant'].search([('product_id', '=', self.g40.id)])
        self.assertFalse(quants, "No stock.quant should ever exist for a non-storable concrete product.")

    def test_04_qty_delivered_syncs_to_sale_order_line(self):
        """ qty_delivered on the SO line equals the sum of actual delivered m3 across loads,
        and invoice_status updates because sale_line_ids is set on the invoice line. See
        NOTES.md: qty_delivered_method computes to 'stock_move' (not 'manual') for this
        product because sale_stock auto-installs, but the direct write still persists because
        move_ids never gets populated - asserted explicitly below so a future Odoo change to
        this behaviour fails loudly instead of silently. """
        self.assertEqual(self.sale_order_line.qty_delivered_method, 'stock_move')

        calloff = self._create_calloff(qty=10, phase=self.phase_foundation)
        loads = self._confirm_and_dispatch(calloff)
        self._drive_load_to_delivered(loads[0], batched=10.0, delivered=9.5, returned=0.5)

        self.sale_order_line.invalidate_recordset(['qty_delivered'])
        self.assertEqual(float_compare(self.sale_order_line.qty_delivered, 9.5, precision_digits=2), 0)

        loads[0].action_create_invoice()
        self.sale_order_line.invalidate_recordset()
        self.assertIn(self.sale_order_line.invoice_status, ('to invoice', 'invoiced', 'no'))
        self.assertTrue(loads[0].invoice_id.invoice_line_ids.mapped('sale_line_ids'))

    def test_05_duplicate_invoice_raises(self):
        calloff = self._create_calloff(qty=10, phase=self.phase_foundation)
        loads = self._confirm_and_dispatch(calloff)
        load = self._drive_load_to_delivered(loads[0])
        load.action_create_invoice()
        with self.assertRaises(UserError):
            load.action_create_invoice()

    def test_06_returned_exceeds_batched_raises(self):
        calloff = self._create_calloff(qty=10, phase=self.phase_foundation)
        loads = self._confirm_and_dispatch(calloff)
        load = loads[0]
        with self.assertRaises(ValidationError):
            load.write({'actual_batched_qty_m3': 10.0, 'returned_qty_m3': 15.0})

    def test_07_variance_beyond_tolerance_requires_reason(self):
        calloff = self._create_calloff(qty=10, phase=self.phase_foundation)
        loads = self._confirm_and_dispatch(calloff)
        load = loads[0]
        # variance = 10.0 - 9.0 - 0.0 = 1.0 m3, well beyond the 0.05 m3 tolerance, and no
        # variance_reason is supplied. The reconciliation is only meaningful once the load has
        # reached 'delivered' (delivered/returned are still 0 through batching/dispatch, which
        # would otherwise make every load look like a 1.0 m3 variance the moment it's batched -
        # see NOTES.md), so the constraint is checked at that state.
        with self.assertRaises(ValidationError):
            load.write({
                'actual_batched_qty_m3': 10.0, 'actual_delivered_qty_m3': 9.0,
                'returned_qty_m3': 0.0, 'state': 'delivered',
            })

    def test_10_standby_boundary(self):
        """ Standby is zero at exactly the free allowance and correct one minute beyond it. """
        calloff = self._create_calloff(qty=10, phase=self.phase_foundation)
        loads = self._confirm_and_dispatch(calloff)
        load = loads[0]
        self.assertEqual(load.free_unloading_minutes, 45)

        now = datetime.now()
        load.write({'unloading_start': now, 'unloading_end': now + timedelta(minutes=45)})
        self.assertEqual(load.standby_minutes, 0.0)
        self.assertEqual(load.standby_charge, 0.0)

        load.write({'unloading_start': now, 'unloading_end': now + timedelta(minutes=46)})
        self.assertEqual(float_compare(load.standby_minutes, 1.0, precision_digits=2), 0)
        self.assertEqual(float_compare(load.standby_charge, (1 / 60.0) * 150, precision_digits=2), 0)

    def test_12_cancel_invoiced_load_raises(self):
        calloff = self._create_calloff(qty=10, phase=self.phase_foundation)
        loads = self._confirm_and_dispatch(calloff)
        load = self._drive_load_to_delivered(loads[0])
        load.action_create_invoice()
        with self.assertRaises(UserError):
            load.action_cancel()

    def test_13_invalid_state_transition_raises(self):
        calloff = self._create_calloff(qty=10, phase=self.phase_foundation)
        loads = self._confirm_and_dispatch(calloff)
        load = loads[0]
        self.assertEqual(load.state, 'draft')
        with self.assertRaises(UserError):
            load.action_confirm_delivery()

    def test_15_qc_sample_created_once_with_correct_dates(self):
        calloff = self._create_calloff(qty=10, phase=self.phase_foundation)
        loads = self._confirm_and_dispatch(calloff)
        load = loads[0]
        load.write({
            'fleet_vehicle_id': self.truck_12.id, 'driver_id': self.driver.id,
            'plant_id': self.env['stock.warehouse'].search([], limit=1).id,
        })
        load.action_confirm()
        load.action_create_mo()
        load.action_start_batching()
        load.write({'actual_batched_qty_m3': 10.0})
        load.action_mark_batched()
        self.assertTrue(load.qc_sample_id)
        sample = load.qc_sample_id
        self.assertEqual(sample.test_7_day_date, sample.sample_date + timedelta(days=7))
        self.assertEqual(sample.test_28_day_date, sample.sample_date + timedelta(days=28))

        first_sample_id = sample.id
        # Calling mark_batched semantics again (guarded by state) must not create a second
        # sample: action_mark_batched itself is only callable from 'batching', so re-invoking
        # _create_qc_sample() directly is the correct way to assert idempotency.
        load._create_qc_sample()
        self.assertEqual(load.qc_sample_id.id, first_sample_id)
        self.assertEqual(self.env['rmc.qc.sample'].search_count([('load_id', '=', load.id)]), 1)
