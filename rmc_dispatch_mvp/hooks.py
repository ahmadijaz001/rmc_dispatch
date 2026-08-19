import logging
from datetime import timedelta

from odoo import fields

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    """ Drive the demo call-off through its real state machine (button methods), rather than
    fabricating a 'done' state by writing fields directly, per the spec's working method.
    No-op on a database with no demo data (e.g. a production install). """
    calloff = env.ref('rmc_dispatch_mvp.demo_calloff_foundation', raise_if_not_found=False)
    if not calloff:
        return
    # Every RMC action/field is gated behind one of the six RMC groups (finance, dispatcher,
    # manager, ...) - none of which the default administrator is a member of by default (they
    # are not implied by base.group_system). Without this, someone exploring the demo sees empty
    # menus and missing buttons (e.g. no "Create Invoice") and has no obvious way to know why.
    admin_user = env.ref('base.user_admin', raise_if_not_found=False)
    manager_group = env.ref('rmc_dispatch_mvp.group_rmc_manager', raise_if_not_found=False)
    if admin_user and manager_group:
        admin_user.group_ids = [(4, manager_group.id)]
    _run_demo_workflow(env, calloff)


def _run_demo_workflow(env, calloff):
    calloff.action_check_credit()
    calloff.action_confirm()
    calloff.action_create_loads()

    loads = calloff.load_ids.sorted('id')
    if len(loads) < 2:
        _logger.warning("RMC demo: expected two loads to be created from the demo call-off; skipping the rest of the demo workflow.")
        return
    load_1, load_2 = loads[0], loads[1]

    vehicle_12 = env.ref('rmc_dispatch_mvp.demo_fleet_vehicle_truck_12', raise_if_not_found=False)
    driver_ali = env.ref('rmc_dispatch_mvp.demo_partner_driver_ali', raise_if_not_found=False)
    now = fields.Datetime.now()

    load_1.write({
        'fleet_vehicle_id': vehicle_12.id if vehicle_12 else False,
        'driver_id': driver_ali.id if driver_ali else False,
        'scheduled_departure': now - timedelta(hours=3),
    })
    load_1.action_confirm()
    load_1.action_create_mo()
    load_1.action_start_batching()
    load_1.write({'actual_batched_qty_m3': 10.0})
    load_1.action_mark_batched()
    load_1.write({'actual_departure': now - timedelta(hours=2, minutes=30)})
    load_1.action_dispatch()
    load_1.write({'site_arrival': now - timedelta(hours=2)})
    load_1.action_mark_arrived()
    load_1.write({
        'unloading_start': now - timedelta(hours=1, minutes=40),
        'unloading_end': now - timedelta(hours=0, minutes=30),
        'return_to_plant_datetime': now - timedelta(minutes=10),
        'actual_delivered_qty_m3': 9.5,
        'returned_qty_m3': 0.5,
        'returned_reason_id': env.ref('rmc_dispatch_mvp.return_reason_site_excess').id,
        'signed_by': 'Site Engineer',
    })
    load_1.action_confirm_delivery()
    load_1.action_create_invoice()

    if load_1.qc_sample_id:
        load_1.qc_sample_id.write({
            'slump_mm': 120,
            'concrete_temperature_c': 28,
            'ambient_temperature_c': 34,
            'test_7_day_result': 32.0,
        })

    # load_2 is deliberately left at 'scheduled' per the spec's demo data description.
    vehicle_18 = env.ref('rmc_dispatch_mvp.demo_fleet_vehicle_truck_18', raise_if_not_found=False)
    load_2.write({
        'fleet_vehicle_id': vehicle_18.id if vehicle_18 else False,
        'driver_id': driver_ali.id if driver_ali else False,
        'scheduled_departure': now + timedelta(hours=1),
    })
    load_2.action_confirm()
