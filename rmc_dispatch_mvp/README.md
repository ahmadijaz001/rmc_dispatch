# RMC Dispatch MVP

## Purpose

`rmc_dispatch_mvp` connects a customer construction project and its phases to daily concrete
call-offs, truck loads, manufacturing, delivery and invoicing for a Ready-Mix Concrete (RMC)
producer.

**One business object is the operational source of truth:**

```
rmc.load  =  one truck load  =  one delivery ticket
```

Every other document (manufacturing order, invoice, QC sample) links to `rmc.load`. The
sales order stays in the chain purely for contracted quantity, delivered-quantity write-back
and invoice linkage - it does not drive price (see "Design decisions" below).

## Odoo 19 only, Community Edition

This module targets **Odoo 19.0 Community only** and installs and runs on a clean Odoo 19
database with no Enterprise addons present. It was built and verified against this Odoo 19
Community source tree, cross-checking every native field/method it depends on before use (see
`NOTES.md` for the full list of verified names and the Odoo-19-specific deviations this forced).

Enterprise-only features (a Gantt dispatch board, Odoo's own Quality module, Approvals,
Documents, asset-based fleet costing) belong in a separate bridge module,
`rmc_dispatch_enterprise` (not part of this module), licensed `OPL-1`. A module that depends on
Enterprise code cannot be LGPL-3, and this module is meant to stay sellable to, and testable on,
Community-only prospects.

## Dependencies

```
base, mail, product, uom, sale_management, project, stock, mrp, fleet, account
```

**Deliberately not depended on:**
- `quality`, `quality_control`, `quality_mrp` - Enterprise-only; QC is handled by our own
  `rmc.qc.sample` model instead.
- `hr` - heavily restructured in Odoo 19 (`hr.contract` became `hr.version`) and not needed;
  drivers are plain `res.partner` records, matching `fleet.vehicle.driver_id`'s own domain.
- `sale_project` - would auto-create a project per sales order and pull in milestone/timesheet
  machinery we don't want; `rmc.calloff`/`rmc.load` carry their own `project_id`/`phase_id`.

`analytic` is pulled in transitively by `account` (and by `project`, which already has its own
`account_id` field for a linked analytic account) - not listed separately since this module does
not create analytic plans of its own.

## Installation

1. Copy (or symlink) this module into an addons path alongside `base`, `product`, `uom`,
   `sale_management`, `project`, `stock`, `mrp`, `fleet`, `account`.
2. Install `rmc_dispatch_mvp` from Apps. Demo data (`--with-demo` - see note below) seeds a
   full worked example matching the click-through in "Demo workflow" below.
3. Assign users to the six RMC security groups under Settings > Users, or via the demo users
   described in "Security matrix".

**Odoo 19 demo-data note:** unlike Odoo ≤18, demo data is no longer loaded by default. Pass
`--with-demo` on `odoo-bin` (or tick "Demo Data" in the database manager) to get the worked
example; installing without it is equally supported and leaves every model empty and ready for
real data entry.

## Data model

```
                         rmc.project.rate  (price authority)
                                |  resolved on draft, frozen on confirm
                                v
sale.order ──────────────> rmc.calloff ─────copy on load creation────> rmc.load
(qty authority,             (daily request,                            │  │  │  │
 qty_delivered/             frozen price)                              │  │  │  └─> rmc.qc.sample
 sale_line_ids sync)                                                   │  │  └────> account.move (invoice)
                                                                        │  └───────> mrp.production
                                                                        └──────────> fleet.vehicle / res.partner (driver)
```

- `rmc.project.rate` - the commercial source of truth for price per project/phase/grade.
- `rmc.calloff` - a customer's daily concrete request; resolves and freezes price on
  confirmation, splits into loads.
- `rmc.load` - **the operational source of truth**; one truck load, one delivery ticket, one
  optional manufacturing order, one optional invoice, one QC sample.
- `rmc.qc.sample` - cube/slump QC record, auto-created when a load finishes batching.
- `rmc.return.reason` - configurable reasons a load can be returned, optionally chargeable.

## Demo workflow (also the acceptance-test click-through)

1. Create project **Dubai Tower A** with phases Foundation, Ground Floor Slab, Columns.
2. Create and confirm a sales order for **120 m³ G40** linked to the project (the quantity
   commitment - not the price).
3. Two project rate cards exist: Dubai Tower A + G40 at **AED 280/m³** (project-level), and
   Dubai Tower A + Foundation + G40 at **AED 290/m³** (phase-level exception).
4. Create a 20 m³ call-off against Foundation. It resolves to **AED 290/m³**
   (`rate_source = project_rate_card`) because the phase-level card beats the project-level
   one. Check credit (approved - the demo enables the company's credit-limit check and gives
   the customer a high limit). Confirm - the rate fields become read-only.
5. Create loads - two 10 m³ loads are generated, `RMC/<year>/00001` and `00002`, both carrying
   the frozen 290 rate.
6. Assign Truck 12, confirm load 1, create its MO (one MO, the G40 BoM, `consumption='flexible'`).
7. Start batching, mark batched at 10.0 m³ - the MO completes with no consumption-warning
   popup even though actual component consumption differs from the BoM ratio.
8. A QC sample is auto-created with 7-day and 28-day test dates.
9. Dispatch, mark arrived, unloading runs 70 minutes against a 45-minute free allowance.
10. Confirm delivery: 9.5 m³ delivered, 0.5 m³ returned as "Site excess", signed.
11. The sale order line now shows 9.5 m³ delivered.
12. Edit the Foundation rate card to AED 320/m³ - reopening load 1 still shows 290 (frozen).
13. Create the invoice: 9.5 m³ × **290** (not 320, not 280) plus a standby line for 25 minutes
    at AED 150/hour. A second invoice attempt is refused.
14. The QC sample navigates sample -> load -> MO -> BoM -> raw component moves via smart
    buttons.
15. No `stock.quant` exists for the G40 concrete product at any point.
16. The Daily Dispatch, Load Margin and Cement Variance reports show load 1 with correct
    numbers.
17. Logging in as each of the six demo users shows the access matrix below in effect; the
    Batch Operator cannot see any rate anywhere in the UI.

## Security matrix

| Group | Access |
|---|---|
| RMC Driver | Reads only own loads (record rule on `driver_id`); can write delivery timestamps, signature, delivered/returned qty (enforced field-by-field in `rmc.load.write()`, not just hidden in the view). No rate visibility. |
| RMC Batch Operator | Reads all loads; writes batching fields and batching state only (same field-level enforcement as above). No invoicing, no rates. |
| RMC QC Technician | Full access to `rmc.qc.sample`; read-only on loads. |
| RMC Dispatcher | Full access to call-offs and loads. No credit override, no invoicing, no cancellation. |
| RMC Finance | Reads loads; creates/reads invoices. No batching or delivery edits. |
| RMC Manager | Everything above (via `implied_ids`), plus credit override, quantity override, cancellation, and rate-card/config management. |

Rate-card access is deliberately narrow: only RMC Manager can create/write/unlink
`rmc.project.rate`; Dispatcher and Finance get read-only; Driver, Batch Operator and QC
Technician get **no access row at all** (no menu, and `search()`/`read()` raise `AccessError`).
Because `agreed_rate_per_m3`, `standby_charge`, `material_cost` and `gross_margin` are stored on
the call-off/load, the `groups=` restrictions on those specific fields are what actually keep
commercial figures out of the Driver/Batch Operator/QC Technician UI - the rate-card access
grant alone would not be enough, since those figures are also visible via the load. Credit
override is additionally enforced in Python (`rmc.credit.override.wizard.action_confirm()`
raises `AccessError` for non-managers even if a determined caller bypassed the view).

## Reports

1. **Delivery ticket** (QWeb PDF, `rmc.load`) - load number, date, customer, project/site,
   phase, truck plate, driver, grade, DCL/mix approval reference, planned/batched/
   delivered/returned m³, batch/departure/arrival/unloading timestamps, standby minutes,
   water-added declaration, QC sample reference, signature image, signed-by name, and a
   disclaimer. Custom paperformat.
2. **Daily Dispatch Report** - pivot/list on `rmc.load` grouped by date and plant; scheduled/
   batched/delivered/returned m³, load count, average cycle time, average standby. Truck count
   is obtained by grouping rows on Truck rather than a distinct-count measure (pivot views
   don't support count-distinct).
3. **Load Margin Analysis** (`rmc.load.margin.report`, `_auto=False`) - sales value, standby
   revenue, material cost, gross margin, margin % by load/project/grade/customer/date.
4. **Cement Variance** (`rmc.cement.variance.report`, `_auto=False`) - design vs actual
   kg/m³ of cement, variance and variance %, by load/grade/plant/date.

Both SQL report views select directly from `rmc.load`'s own stored costing/cement-variance
columns rather than re-deriving the MO/BoM/stock-move joins a second time in raw SQL - see
"Design decisions" below for why.

## Design decisions and assumptions

**Price authority (rate card) vs quantity authority (sales order) are deliberately not
collapsed into one model.** In RMC, price is negotiated per project (and sometimes per phase)
independently of the contracted quantity: the sales order tracks *how much* concrete the
customer has committed to, drives credit exposure, and receives the invoice linkage via
`sale_line_ids`; the rate card tracks *what it costs*, which can change (or have exceptions)
without touching the contracted quantity at all.

**Price is frozen at call-off confirmation, never read live.** A dispatcher confirming a
call-off today must get today's price even if the rate card is edited next week, and a load
delivered late must still bill at the rate agreed when the customer called it off - not
whatever the rate card says on the delivery date. `agreed_rate_per_m3`, `free_unloading_minutes`
and `standby_rate_per_hour` are plain stored fields on `rmc.calloff`, populated by an imperative
`_resolve_rate()` method (called from `create()`, from `write()` on project/phase/product/date
changes while still `draft`, and once more from `action_confirm()`) - deliberately **not**
declared as `compute=` fields, even though the original functional spec labelled them that way.
A true Odoo compute field re-evaluates automatically whenever its declared dependencies change,
including a rate card edited after confirmation, which would silently break the freeze. This is
verified by `test_19_price_freeze`.

**The sale-order-rate fallback is disabled by default** (`rmc_allow_so_rate_fallback` on
`res.company`). A silent fallback to whatever price happens to sit on the sales order means a
manager who forgot to create a rate card invoices at a placeholder price, and nobody notices
until the customer disputes it. When the fallback is used it is visible (a warning banner on the
call-off, a chatter message naming the user) and traceable (`rate_source = 'sale_order_line'`).

**The overlap constraint on rate cards is a Python `@api.constrains`, not a SQL unique
index.** Date-range overlap is not expressible as a SQL unique constraint, and in PostgreSQL
`NULL != NULL`, so a unique index on `(company_id, project_id, phase_id, product_id)` would
happily allow two *different* project-level cards with `phase_id IS NULL` to coexist - the
opposite of what's needed. The Python constraint treats a null `phase_id` as its own distinct
scope (a project-level card never conflicts with a phase-level one) and only rejects genuine
date-range overlaps within the same scope.

**Concrete is non-storable, and that is the point, not a limitation.** Concrete products are
`type='consu'` with `is_storable=False`. The MO produces it, raw components are consumed and
valued normally, but no `stock.quant` and no stock valuation is ever created for the concrete
itself - because physically none should exist; concrete is produced and delivered the same day.

**`stock.picking` is not used in this MVP.** With a non-storable product a delivery order would
carry no useful information (there is nothing to pick, move, or value). `rmc.load` *is* the
delivery document: it carries the planned/batched/delivered/returned quantities, timestamps and
signature that a picking would otherwise hold. `rmc.load.picking_id` is declared as a nullable
placeholder for a possible future phase and is never set by this module.

**The sales order stays in sync via `qty_delivered` + `sale_line_ids`, not a custom ledger.**
Every concrete invoice line sets `sale_line_ids = [(4, sale_order_line_id)]`, so Odoo's native
`qty_invoiced` and `invoice_status` update themselves - this is what prevents someone re-invoicing
the same concrete from the Sales app. `qty_delivered` is written directly with the sum of
*actual* delivered m³ across all non-cancelled loads on that line whenever a delivery is
confirmed. **Odoo-19-specific subtlety** (see `NOTES.md` for the full trace): because
`sale_stock` auto-installs whenever both `sale_management` and `stock` are present, and its
override of `_compute_qty_delivered_method` forces `qty_delivered_method = 'stock_move'` for
*any* `product.type == 'consu'` (storable or not), this field does **not** compute to
`'manual'` as a naive reading of the spec would assume. The direct write still works and
persists, because the `'stock_move'` compute sums real `stock.move` records against the line
and this line never has any (no picking is ever created) - so the compute contributes `0.0` and
never re-triggers. `test_04_qty_delivered_syncs_to_sale_order_line` asserts the actual value of
`qty_delivered_method` explicitly, so a future Odoo change to this behaviour fails the test
loudly instead of silently corrupting delivered quantities.

**The BoM `consumption='flexible'` requirement.** Aggregate and sand quantities are corrected
for moisture on every single load, so actual raw-material consumption differs from the BoM on
every load, without exception. `mrp.production.button_mark_done()` returns a
`mrp.consumption.warning` wizard action instead of completing whenever the BoM's consumption
setting isn't `'flexible'` and actual quantities differ - which would block closing every MO in
production. A `mrp.bom` constraint enforces `consumption == 'flexible'` for any BoM whose
product is RMC concrete. `rmc.load.action_mark_batched()` additionally passes
`skip_consumption=True` in the context as a second, independent safety net - and, discovered by
reading `mrp.production.pre_button_mark_done()` rather than assumed from the spec,
**`skip_backorder=True`** as well, because `qty_producing` (actual batched m³) legitimately
differs from `product_qty` (planned m³) and would otherwise raise a second, separate backorder
wizard. If `button_mark_done()` ever returns anything other than `True`, `action_mark_batched()`
raises a clear `UserError` rather than silently leaving the MO open.

**Free-unloading-minutes precedence** (same order for the standby rate): project rate card ->
sale order line -> product default -> company setting -> a hard-coded 45-minute fallback. Both
values are resolved once, together with the price, and frozen on call-off confirmation - never
recomputed on the load from live configuration.

**The state model was collapsed from the original spec's 11 states to 8**
(`draft, scheduled, batching, batched, in_transit, delivered, invoiced, cancelled`).
`dispatched` is the same event as `in_transit` (the departure timestamp); "arrived" is a
timestamp (`site_arrival`), not a lifecycle stage, since nothing else about the load changes
when the truck arrives; and "returned" is a quantity on a delivered load
(`returned_qty_m3`/`returned_reason_id`), not a separate stage - a fully returned load is still
`delivered` with `actual_delivered_qty_m3 = 0`.

## Odoo 19 deviations forced by the source (see NOTES.md for the full, cited list)

- `res.groups.category_id` no longer exists; group categorisation is
  `res.groups.privilege_id -> res.groups.privilege.category_id -> ir.module.category`.
- UoM categories were removed; `uom.uom.relative_uom_id`/`relative_factor` replace
  `category_id`/`factor`. The core m³ UoM (`uom.product_uom_cubic_meter`) already exists but
  ships archived - this module activates it rather than creating a duplicate.
- `product.template.uom_po_id` (separate purchase UoM) no longer exists; there is a single
  `uom_id`.
- `stock.valuation.layer` no longer exists; `stock.move.value` (a Monetary field) plus the new
  `product.value` model replace it. `rmc.load.material_cost` sums `move_raw_ids.value`.
- `qty_delivered_method` computing to `'stock_move'` rather than `'manual'` for a non-storable
  `consu` product once `sale_stock` auto-installs - see "Design decisions" above.
- Demo data is opt-in (`--with-demo`) rather than opt-out (`--without-demo`) as of Odoo 19.
- Search-view group-by `<group>` elements no longer accept `expand=`/`string=` attributes; the
  "Group By" header and collapse state are now handled entirely client-side.

## Known MVP limitations

- No plant PLC/OPC/ODBC integration with the batching system - all batched/actual quantities
  are entered manually by the batch operator.
- No GPS or geofencing; `fleet.vehicle.next_available_datetime` is a fixed planning estimate
  (120 minutes) set on dispatch and freely editable, not a measurement.
- No driver mobile app; drivers use the same backend form, restricted by the security matrix.
- No customer portal.
- No weighbridge or silo sensor integration.
- No UAE e-invoicing (ZATCA/FTA-style e-invoice generation) - invoices are standard Odoo
  `account.move` records in `draft`.
- Single-company assumptions in the two SQL report views (they carry `company_id` for record
  rules but are not built to aggregate meaningfully across companies).
- No fleet cost allocation engine (fuel, maintenance, depreciation are out of scope) and no
  dispatch/route scheduling optimisation - the dispatch board is a manual kanban, not a solver.
- The reports assume a single batching plant per load; a multi-plant blending scenario for one
  load is out of scope.
- `fleet.vehicle.vehicle_type` only offers `'car'`/`'bike'` in this Odoo 19 core `fleet` module
  (no `'truck'`); mixer trucks are recorded as `vehicle_type='car'`, which is cosmetic only -
  `capacity_m3`/`ready_for_dispatch` (this module's own fields) are what actually drive
  dispatch logic.
