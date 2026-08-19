from datetime import datetime, timedelta

from odoo.tests.common import TransactionCase


class RmcTestCommon(TransactionCase):
    """ Shared fixtures: customer, project/phases, raw materials + BoM, G40 concrete grade,
    sale order, and the two demo-style rate cards (project-level 280, Foundation phase 290).
    Mirrors demo/rmc_demo_data.xml so behaviour verified in tests matches the demo/acceptance
    click-through in the README. """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.account_use_credit_limit = True
        cls.company.rmc_default_load_size_m3 = 10.0
        cls.company.rmc_standby_service_product_id = cls.env['product.product'].create({
            'name': 'RMC Standby / Waiting Time', 'type': 'service', 'list_price': 150,
        })

        cls.customer = cls.env['res.partner'].create({
            'name': 'ABC Contracting',
            'is_company': True,
            'credit_limit': 500000,
        })
        cls.driver = cls.env['res.partner'].create({'name': 'Ali Driver'})

        cls.project = cls.env['project.project'].create({
            'name': 'Dubai Tower A',
            'partner_id': cls.customer.id,
        })
        cls.phase_foundation = cls.env['project.task'].create({
            'name': 'Foundation', 'project_id': cls.project.id})
        cls.phase_columns = cls.env['project.task'].create({
            'name': 'Columns', 'project_id': cls.project.id})

        kg = cls.env.ref('uom.product_uom_kgm')
        litre = cls.env.ref('uom.product_uom_litre')
        m3 = cls.env.ref('uom.product_uom_cubic_meter')

        cls.cement = cls.env['product.product'].create({
            'name': 'OPC Cement', 'type': 'consu', 'is_storable': True,
            'uom_id': kg.id, 'rmc_material_type': 'cement',
        })
        cls.ggbs = cls.env['product.product'].create({
            'name': 'GGBS', 'type': 'consu', 'is_storable': True,
            'uom_id': kg.id, 'rmc_material_type': 'scm',
        })
        cls.aggregate = cls.env['product.product'].create({
            'name': '20mm Aggregate', 'type': 'consu', 'is_storable': True,
            'uom_id': kg.id, 'rmc_material_type': 'aggregate',
        })
        cls.sand = cls.env['product.product'].create({
            'name': 'Crushed Sand', 'type': 'consu', 'is_storable': True,
            'uom_id': kg.id, 'rmc_material_type': 'sand',
        })
        cls.water = cls.env['product.product'].create({
            'name': 'Water', 'type': 'consu', 'is_storable': True,
            'uom_id': litre.id, 'rmc_material_type': 'water',
        })
        cls.admixture = cls.env['product.product'].create({
            'name': 'Admixture', 'type': 'consu', 'is_storable': True,
            'uom_id': kg.id, 'rmc_material_type': 'admixture',
        })

        for product, qty in ((cls.cement, 50000), (cls.ggbs, 50000), (cls.aggregate, 200000),
                              (cls.sand, 200000), (cls.water, 100000), (cls.admixture, 5000)):
            cls.env['stock.quant'].create({
                'product_id': product.id,
                'location_id': cls.env.ref('stock.stock_location_stock').id,
                'quantity': qty,
            })

        cls.g40 = cls.env['product.product'].create({
            'name': 'Concrete G40', 'type': 'consu', 'is_storable': False,
            'is_rmc_concrete': True, 'concrete_grade': 'G40', 'uom_id': m3.id,
            'target_strength': 40, 'default_free_unloading_minutes': 45,
            'mix_approval_reference': 'DCL/MD/2026/G40-001',
        })
        cls.bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.g40.product_tmpl_id.id,
            'product_qty': 1, 'product_uom_id': m3.id,
            'consumption': 'flexible', 'type': 'normal',
            'approval_reference': 'DCL/MD/2026/G40-001',
            'target_strength': 40,
            'bom_line_ids': [
                (0, 0, {'product_id': cls.cement.id, 'product_qty': 280, 'product_uom_id': kg.id}),
                (0, 0, {'product_id': cls.ggbs.id, 'product_qty': 120, 'product_uom_id': kg.id}),
                (0, 0, {'product_id': cls.aggregate.id, 'product_qty': 1050, 'product_uom_id': kg.id}),
                (0, 0, {'product_id': cls.sand.id, 'product_qty': 750, 'product_uom_id': kg.id}),
                (0, 0, {'product_id': cls.water.id, 'product_qty': 150, 'product_uom_id': litre.id}),
                (0, 0, {'product_id': cls.admixture.id, 'product_qty': 4, 'product_uom_id': kg.id}),
            ],
        })

        cls.vehicle_model = cls.env['fleet.vehicle.model'].create({
            'name': 'Concrete Mixer',
            'brand_id': cls.env['fleet.vehicle.model.brand'].create({'name': 'RMC Fleet'}).id,
            'vehicle_type': 'car',
        })
        cls.truck_12 = cls.env['fleet.vehicle'].create({
            'model_id': cls.vehicle_model.id, 'license_plate': 'TRUCK-12',
            'driver_id': cls.driver.id, 'capacity_m3': 10, 'ready_for_dispatch': True,
        })
        cls.truck_18 = cls.env['fleet.vehicle'].create({
            'model_id': cls.vehicle_model.id, 'license_plate': 'TRUCK-18',
            'driver_id': cls.driver.id, 'capacity_m3': 10, 'ready_for_dispatch': True,
        })

        cls.sale_order = cls.env['sale.order'].create({'partner_id': cls.customer.id})
        cls.sale_order_line = cls.env['sale.order.line'].create({
            'order_id': cls.sale_order.id, 'product_id': cls.g40.id,
            'product_uom_qty': 120, 'product_uom_id': m3.id, 'price_unit': 280,
            'rmc_project_id': cls.project.id,
        })
        cls.sale_order.action_confirm()

        today = datetime.today().date()
        cls.rate_project = cls.env['rmc.project.rate'].create({
            'project_id': cls.project.id, 'product_id': cls.g40.id,
            'rate_per_m3': 280, 'free_unloading_minutes': 45, 'standby_rate_per_hour': 150,
            'date_from': today.replace(month=1, day=1),
            'sale_order_id': cls.sale_order.id, 'sale_order_line_id': cls.sale_order_line.id,
        })
        cls.rate_foundation = cls.env['rmc.project.rate'].create({
            'project_id': cls.project.id, 'phase_id': cls.phase_foundation.id, 'product_id': cls.g40.id,
            'rate_per_m3': 290, 'free_unloading_minutes': 45, 'standby_rate_per_hour': 150,
            'date_from': today.replace(month=1, day=1),
            'sale_order_id': cls.sale_order.id, 'sale_order_line_id': cls.sale_order_line.id,
        })

    def _create_calloff(self, qty=20.0, phase=None, when=None):
        phase = phase or self.phase_foundation
        return self.env['rmc.calloff'].create({
            'customer_id': self.customer.id,
            'sale_order_id': self.sale_order.id,
            'sale_order_line_id': self.sale_order_line.id,
            'project_id': self.project.id,
            'phase_id': phase.id,
            'product_id': self.g40.id,
            'requested_qty': qty,
            'requested_delivery_datetime': when or datetime.now(),
        })

    def _confirm_and_dispatch(self, calloff):
        calloff.action_check_credit()
        calloff.action_confirm()
        calloff.action_create_loads()
        return calloff.load_ids.sorted('id')

    def _drive_load_to_delivered(self, load, batched=10.0, delivered=9.5, returned=0.5,
                                  unloading_minutes=70):
        load.write({
            'fleet_vehicle_id': self.truck_12.id, 'driver_id': self.driver.id,
            'plant_id': self.env['stock.warehouse'].search([], limit=1).id,
            'scheduled_departure': datetime.now(),
        })
        load.action_confirm()
        load.action_create_mo()
        load.action_start_batching()
        load.write({'actual_batched_qty_m3': batched})
        load.action_mark_batched()
        now = datetime.now()
        load.write({'actual_departure': now - timedelta(hours=2)})
        load.action_dispatch()
        load.write({'site_arrival': now - timedelta(hours=1, minutes=30)})
        load.action_mark_arrived()
        load.write({
            'unloading_start': now - timedelta(minutes=unloading_minutes),
            'unloading_end': now,
            'return_to_plant_datetime': now + timedelta(minutes=10),
            'actual_delivered_qty_m3': delivered,
            'returned_qty_m3': returned,
            'returned_reason_id': (
                self.env.ref('rmc_dispatch_mvp.return_reason_site_excess').id if returned else False),
            'signed_by': 'Site Engineer',
        })
        load.action_confirm_delivery()
        return load

    def _create_user(self, name, login, group_xmlids):
        """ Always includes base.group_user so security tests exercise our own RMC access
        rules, not incidental gaps in baseline internal-user access to unrelated models. """
        group_ids = [self.env.ref(xmlid).id for xmlid in group_xmlids]
        group_ids.append(self.env.ref('base.group_user').id)
        return self.env['res.users'].create({
            'name': name, 'login': login, 'email': f'{login}@example.com',
            'group_ids': [(6, 0, group_ids)],
        })
