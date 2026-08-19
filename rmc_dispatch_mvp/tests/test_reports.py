from odoo.tests import tagged
from odoo.tools.float_utils import float_compare

from .common import RmcTestCommon


@tagged('post_install', '-at_install')
class TestReports(RmcTestCommon):

    def test_14_report_views_return_correct_totals(self):
        calloff = self._create_calloff(qty=10, phase=self.phase_foundation)
        loads = self._confirm_and_dispatch(calloff)
        load = self._drive_load_to_delivered(loads[0], batched=10.0, delivered=9.5, returned=0.5,
                                              unloading_minutes=70)

        margin_rows = self.env['rmc.load.margin.report'].search([('load_id', '=', load.id)])
        self.assertEqual(len(margin_rows), 1)
        row = margin_rows[0]
        expected_sales_value = 9.5 * 290
        self.assertEqual(float_compare(row.sales_value, expected_sales_value, precision_digits=2), 0)
        self.assertTrue(row.standby_revenue > 0)
        self.assertTrue(row.material_cost >= 0)
        self.assertEqual(float_compare(row.gross_margin, row.sales_value + row.standby_revenue - row.material_cost, precision_digits=2), 0)

        variance_rows = self.env['rmc.cement.variance.report'].search([('load_id', '=', load.id)])
        self.assertEqual(len(variance_rows), 1)
        vrow = variance_rows[0]
        self.assertEqual(float_compare(vrow.design_cement_kg_per_m3, 280, precision_digits=1), 0)
        self.assertEqual(float_compare(vrow.batched_qty_m3, 10.0, precision_digits=2), 0)
