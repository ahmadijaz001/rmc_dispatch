# Implementation notes — verified against installed Odoo 19.0 source

This file is updated as the module is built. Every entry below was confirmed by grepping
`D:\odoo-19.0\odoo\addons\base`, `D:\odoo-19.0\addons\*` before the corresponding code was
written, per the working method in the spec.

## Confirmed field/API names

- `res.groups.privilege` exists (`odoo/addons/base/models/res_groups_privilege.py`):
  `category_id` (Many2one `ir.module.category`), `group_ids` (One2many `res.groups`, inverse
  `privilege_id`), `sequence`.
- `res.groups.privilege_id` (Many2one `res.groups.privilege`) confirmed on `res.groups`
  (`res_groups.py:36`). `res.groups.category_id` does **not** exist any more — confirmed absent.
- `group_ids` (Many2many `res.groups`) confirmed on `ir.ui.menu`, `ir.ui.view`,
  `ir.actions.act_window`, `ir.actions.server`, `ir.actions.report`, `res.users`.
- `product.template.type` selection is exactly `[('consu','Goods'),('service','Service'),
  ('combo','Combo')]` (`product_template.py:54`). No `'product'` value exists.
- `is_storable` (Boolean) lives on `product.template` in the **`stock`** module
  (`addons/stock/models/product.py:821`), not in `product`. It is
  `compute='compute_is_storable', readonly=False, store=True, precompute=True` — i.e. a
  "computed but directly writable" field, same pattern as `qty_delivered` below. Direct
  `create()`/`write()` with `is_storable=False` is fully supported.
- `uom.uom`: `relative_factor` (Float) and `relative_uom_id` (Many2one `uom.uom`,
  `ondelete='cascade'`) confirmed (`addons/uom/models/uom_uom.py`). There is **no**
  `category_id` / `uom.category` model any more.
- `sale.order.line.product_uom_id` and `sale.order.line.tax_ids` confirmed
  (`addons/sale/models/sale_order_line.py:132,162`).
- `mrp.bom.consumption` selection `[('flexible','Allowed'),('warning','Allowed with warning'),
  ('strict','Blocked')]`, default `'warning'` (`addons/mrp/models/mrp_bom.py:70`). Same field
  (as a related/selection) exists on `mrp.production`.
- `mrp.production.button_mark_done()` calls `pre_button_mark_done()` first; if that returns
  anything other than `True` it is an action dict (a wizard) and `button_mark_done` returns it
  unexecuted. Two independent context flags must both be passed to guarantee a clean
  completion when actual quantities differ from the BoM:
  - `skip_consumption=True` short-circuits `_get_consumption_issues()` — needed because raw
    material consumption differs from the BoM (moisture correction).
  - `skip_backorder=True` short-circuits `_get_quantity_produced_issues()` — needed because
    `qty_producing` (actual batched m³) can differ from `product_qty` (planned m³) and would
    otherwise raise the backorder wizard. **The spec only mentioned `skip_consumption`; this
    second flag was found by reading `pre_button_mark_done` and is required in this codebase.**
  - `stock.move.quantity` (Float) is the current name for what used to be `quantity_done`;
    confirmed at `addons/stock/models/stock_move.py:171`.
  - `mrp.production.qty_producing` is a plain (non-computed) Float; call
    `production._set_qty_producing()` after setting it to scale `move_raw_ids` quantities.
- `account.res.company.account_use_credit_limit` (Boolean) confirmed
  (`addons/account/models/company.py:159`). `res.partner.credit` (Monetary, compute) and
  `res.partner.credit_limit` (Float) confirmed (`addons/account/models/partner.py`).
- `sale.order.line.qty_delivered` is `compute='_compute_qty_delivered', store=True,
  readonly=False` — directly writable by design (Odoo's "computed but user-editable" pattern:
  a direct `write()` persists and is **not** overwritten unless a dependency of the compute is
  later touched).
- `account.move.line.sale_line_ids` (Many2many `sale.order.line`) confirmed
  (`addons/sale/models/account_move_line.py:12`).
- `account.move.line.analytic_distribution` is `fields.Json` (`addons/account/models/
  account_move_line.py:418`), not a Many2one.
- `fleet.vehicle.driver_id` (Many2one `res.partner`) confirmed.
- `project.task` model confirmed at `_name = 'project.task'` with `project_id` Many2one.
- `models.Constraint(sql, message)` declarative style confirmed in core
  (`res_groups.py:39,41`) — used here instead of `_sql_constraints` throughout.
- `ir.sequence.use_date_range` (Boolean) confirmed — used for the `RMC/%(range_year)s/…`
  sequences.

## Deviations from the spec forced by the actual Odoo 19 source

1. **`qty_delivered_method` is NOT `'manual'` for our concrete product — it is `'stock_move'`.**
   The spec assumed `type='consu'` + non-storable implies `qty_delivered_method == 'manual'`.
   This is false in this codebase: `sale_stock` **auto-installs** whenever both
   `sale_management` and `stock` are present (`addons/sale_stock/__manifest__.py:
   'auto_install': True`), and `sale_stock`'s override of `_compute_qty_delivered_method`
   forces `'stock_move'` for **any** `product.type == 'consu'`, storable or not
   (`addons/sale_stock/models/sale_order_line.py:184-193`).
   Consequence: `_prepare_qty_delivered` for a `'stock_move'` line sums `move_ids` (real
   `stock.move` records against the SO line) and defaults to `0.0` when there are none
   (`addons/sale_stock/models/sale_order_line.py:199-214`). Because our concrete product never
   gets a `stock.picking`/`stock.move` (§3.1), `move_ids` is permanently empty on this line, so
   the stock-move compute contributes nothing and never re-triggers (its `@api.depends` are all
   on `move_ids.*`, which never changes). Our direct `write({'qty_delivered': ...})` therefore
   still sticks exactly as the spec intended — just because the compute never re-runs, not
   because the method is `'manual'`. **This is verified empirically in
   `test_04_qty_delivered_sync`**, which also asserts the actual (surprising) value of
   `qty_delivered_method` so a future Odoo upgrade that changes this behaviour fails loudly
   instead of silently.
2. **`stock.valuation.layer` does not exist in this Odoo 19 codebase.** It has been replaced by
   a `value` (Monetary) field directly on `stock.move`
   (`addons/stock_account/models/stock_move.py:24-26`), populated by the new `product.value`
   model (`addons/stock_account/models/product_value.py`). `material_cost` on `rmc.load` is
   therefore computed as `sum(production.move_raw_ids.mapped('value'))`, not by querying a
   valuation-layer model. This is simpler than the spec assumed, not a limitation.
3. `sale.order.line._compute_invoice_status` (sale_stock override) special-cases
   `invoice_policy == 'delivery'` with done `move_ids` to force "Fully Invoiced" on partial
   delivery. Since our line never has `move_ids`, this branch never fires, so invoicing status
   only reflects `qty_invoiced` vs `qty_delivered` as normal — no special handling needed, but
   documented here since it was checked.

## Still to verify as the module is built
(updated incrementally — see git history of this file / commit messages for the running log)

## Overnight verification pass (2026-08-19) — demo data + full automated test suite

Goal: get `--with-demo` install to complete cleanly end-to-end on a fresh database, then get
the full 22-test suite (`tests/test_rate_resolution.py`, `test_calloff_workflow.py`,
`test_load_workflow.py`, `test_security.py`, `test_reports.py`) to run and pass. Both are now
confirmed working. Findings below.

### Production bugs found and fixed

1. **`rmc.qc.sample` was missing `mail.activity.mixin`.** `_inherit = ['mail.thread']` only —
   `create()` calls `sample.activity_schedule(...)` to schedule the 28-day strength-test
   reminder, but `activity_schedule` is provided by `mail.activity.mixin`, not `mail.thread`.
   Every QC sample creation raised `AttributeError`, which only surfaces once a load is actually
   batched (so it was invisible until the full demo workflow ran end-to-end).
   Fixed in `models/rmc_qc_sample.py`: `_inherit = ['mail.thread', 'mail.activity.mixin']`.

2. **`rmc.load.margin.report` and `rmc.cement.variance.report` could read stale figures.** Both
   are `_auto=False` SQL-view models that `SELECT` directly from `rmc_load`'s own stored compute
   columns (`sales_value`, `standby_charge`, `material_cost`, `gross_margin`, `margin_pct` /
   `design_cement_kg_per_m3`, `actual_cement_kg_per_m3`, `cement_variance_kg_per_m3`) rather than
   re-deriving them via a second join, by deliberate design (see each model's docstring). Nothing
   guaranteed those computed values were flushed to the actual DB row before the raw `SELECT`
   executed — a load costed in the same transaction/request as a report read could show
   pre-update figures. Caught by `test_14_report_views_return_correct_totals` failing
   `sales_value` by exactly the standby-charge amount (report math is correct; the underlying
   `rmc_load` row was stale).
   Fixed by overriding `search_fetch()` on both models to call
   `self.env['rmc.load'].flush_model([...])` for the relevant field names before delegating to
   `super().search_fetch(...)` — `search_fetch` (`odoo/orm/models.py:1383`) is the Odoo 19 ORM's
   common entry point that `search()`/`browse().read()` funnel through, so this covers all normal
   access paths to these reports, not just the direct call the test happened to use.

### Demo-data bugs found and fixed (`demo/rmc_demo_data.xml`)

3. **No `res.company.rmc_standby_service_product_id` configured.** `action_create_invoice()`
   raises `UserError` if a load has a standby charge but no standby product is configured
   (`models/rmc_load.py:459`) — the demo workflow's load 1 always has standby time, so
   `post_init_hook` crashed on `action_create_invoice()`. Fixed by adding a
   `demo_product_standby_service` product (`type='service'`) and setting it on
   `base.main_company`.

4. **No `res.company.rmc_default_load_size_m3` configured.** Falls back to the 8.0 m³ hard-coded
   default in `rmc.calloff.action_create_loads()`. The demo call-off requests 20 m³, which with
   an 8.0 m³ default splits into **three** loads (8 + 8 + 4), not the two loads the demo workflow
   script (`hooks.py`) assumes (it unpacks `load_1, load_2 = loads[0], loads[1]` and hard-codes
   `actual_batched_qty_m3 = 10.0` for load 1, implying a 10 m³ truck size). Fixed by setting
   `rmc_default_load_size_m3 = 10` in demo data, giving exactly two 10 m³ loads.

### Test-suite-only bugs found and fixed (`tests/`) — no production code involved

These only affected the test suite's own fixtures/assertions, not the module users interact
with; listed for completeness since fixing them was part of "get the test suite green":

5. `tests/common.py` `setUpClass` set `rmc_default_load_size_m3 = 8.0`, inconsistent with what
   the tests actually assume (`test_01` expects exactly 2 loads from `qty=20`; most other tests
   use `qty=10` assuming exactly **one** load whose `planned_qty_m3` exactly matches the
   `actual_batched_qty_m3 = 10.0` they hard-code). Fixed to `10.0`.
6. `tests/common.py` `_drive_load_to_delivered()` wrote `returned_qty_m3` and
   `returned_reason_id` in **two separate** `write()` calls — the constraint
   `_check_returned_reason` fired on the first write (reason not set yet) before the second
   statement ever ran. Fixed by merging both fields into one `write()` dict.
7. Same helper never set `signed_by`, which `action_confirm_delivery()` requires
   (`"The delivery must be signed before it can be confirmed."`). Fixed by adding
   `'signed_by': 'Site Engineer'` to the same `write()` dict.
8. Same helper never configured `rmc_standby_service_product_id` on the test company (only the
   demo data did) — broke `action_create_invoice()` in five different tests the same way as
   production bug #3 above. Fixed by creating a standby service product in `setUpClass` and
   assigning it to `cls.company`.
9. `test_security.py::test_11_...` called `calloff.action_confirm()` explicitly and then also
   called the `_confirm_and_dispatch()` helper, which calls `action_confirm()` again — the second
   call raised `"Only a draft call-off can be confirmed."` since the call-off was no longer
   draft. The test also used `qty=10`, which (after fix #5) only produces one load, but the test
   needs two loads (`loads[0]`/`loads[1]` for two different drivers). Fixed by bumping the
   quantity to 20 and replacing the helper call with `calloff.action_create_loads(); loads =
   calloff.load_ids.sorted('id')`.
10. `test_load_workflow.py::test_06_returned_exceeds_batched_raises` set
    `actual_batched_qty_m3` and then `returned_qty_m3` as two separate attribute assignments.
    The intermediate flush (triggered by `assertRaises`' internal `cr.savepoint()`) evaluated the
    variance-reconciliation constraint (`variance_qty_m3 = batched − delivered − returned`) with
    `batched=10, delivered=0, returned=0` — already out of tolerance — raising before the
    intended "returned exceeds batched" assignment ever executed. Fixed by combining both fields
    into a single `write()` call (still raises `ValidationError`, satisfying the test, whichever
    of the two constraints fires first).

### Test-invocation / tooling gotchas (environment, not code)

11. **`--test-enable` + a fresh `-i` install imports test files for the *entire* module
    dependency graph**, not just the tagged module — with `enterprise-19.0` on the addons path
    this pulled in ~150+ modules, and one unrelated, pre-existing broken test import
    (`mrp_workorder_hr_account`'s `tests/test_bom_price.py` importing a module that doesn't
    exist) crashed the whole registry load before our own module's tests ever ran. **Workaround:**
    install the module first without `--test-enable`, then run tests via `-u
    rmc_dispatch_mvp --test-enable --test-tags /rmc_dispatch_mvp` against the already-installed
    database — `-u` only marks the named module (and anything genuinely needing schema/data
    updates) for reload, so unrelated already-installed modules' test files are never re-imported.
12. **Windows/Git-Bash (MSYS) gotcha:** MSYS silently rewrites any command-line argument that
    looks like a POSIX absolute path. `--test-tags /rmc_dispatch_mvp` was rewritten to
    `--test-tags C:/Program Files/Git/rmc_dispatch_mvp`, which Odoo logged as an "Invalid tag"
    and silently ran 0 tests (exit code 0 — easy to mistake for "nothing to test" rather than a
    tooling bug). Fix: prefix the command with `MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL="*"` when
    invoking `odoo-bin` from Git Bash with any leading-`/` argument.

### Final verification results

- **Demo data** (`--with-demo` install on a fresh database): completes with no errors and no
  "demo data failed to install" warning. Independently re-verified via direct SQL against the
  resulting database: exactly 2 loads (`RMC/<year>/00001` fully invoiced with 9.5 m³
  delivered / 0.5 m³ returned, `RMC/<year>/00002` left at `scheduled` per the demo narrative),
  the invoice carries a concrete line at 290/m³ (the Foundation-phase rate, correctly beating the
  280 project-level rate) plus a standby line, a QC sample with 7-day/28-day test dates correctly
  offset from the sample date, and **zero** `stock.quant` rows for the concrete product (it is
  correctly non-storable end to end).
- **Automated test suite**: all 22 numbered tests (§12 of the spec) pass — `0 failed, 0 error(s)
  of 22 tests` — run via `-u rmc_dispatch_mvp --test-enable --test-tags /rmc_dispatch_mvp` on a
  cleanly-installed `rmc_test` database (Enterprise modules present on the addons path, not
  excluded — the `-u`-not-fresh-`-i` workaround above made exclusion unnecessary).
