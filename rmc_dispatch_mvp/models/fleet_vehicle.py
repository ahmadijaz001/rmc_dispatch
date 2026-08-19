from odoo import fields, models


class FleetVehicle(models.Model):
    """ Extension: dispatch-relevant fleet data. No GPS/telematics in the MVP - see README
    "Known MVP limitations". """
    _inherit = 'fleet.vehicle'

    capacity_m3 = fields.Float(
        string='Capacity (m³)', digits='Product Unit',
        help="Maximum concrete volume this mixer truck can carry in one load.")
    ready_for_dispatch = fields.Boolean(
        string='Ready for Dispatch', default=True,
        help="Uncheck to hide this vehicle from dispatch (maintenance, expired permit, etc.).")
    permit_expiry_date = fields.Date(string='Permit Expiry Date')
    insurance_expiry_date = fields.Date(string='Insurance Expiry Date')
    current_load_id = fields.Many2one(
        'rmc.load', string='Current Load', copy=False,
        help="Load this truck is currently carrying, set on dispatch and cleared on delivery.")
    next_available_datetime = fields.Datetime(
        string='Next Available',
        help="Estimated time this truck will be back at plant and available for another load. "
             "Set automatically on dispatch from an estimated cycle time, and freely editable "
             "afterwards by the dispatcher.")
    load_count = fields.Integer(string='Load Count', compute='_compute_rmc_load_count')

    def _compute_rmc_load_count(self):
        load_data = self.env['rmc.load']._read_group(
            [('fleet_vehicle_id', 'in', self.ids)], ['fleet_vehicle_id'], ['__count'])
        counts = {vehicle.id: count for vehicle, count in load_data}
        for vehicle in self:
            vehicle.load_count = counts.get(vehicle.id, 0)
