# Cache reuse and task readiness

## OPF source grade preparation

The source profile cache ignores source observation timestamps and Calendar
targets by period. Physical source content, routing, chunk layout, mappings,
reconciliation policy and accepted evidence still participate in its identity.
Existing reconciliation approval checks, including lookback tolerance, apply
before reuse. Hydrating a cached profile preserves the current observation time.
The cache identity version changed, so older profiles may prepare once again.

## Calendar and optimisation

After validating Calendar and refreshing required opening actuals, the app
compares effective solver inputs with the last completed run. The comparison
includes selected source chemistry and tonnes, chunks, Calendar settings,
solver configuration, site context and guidance file content. Workflow task
submission counters and physical source observation timestamps are excluded.
Actual movement dates and dates within lineage remain significant. Full table
contents are compared, including rows omitted from a printed table preview.

A matching completed run with a saved optimised report advances Calendar
without clearing the displayed result or launching the solver. Starting a new
solve invalidates the previous reuse receipt. Failed, aborted and partial runs
cannot establish a reusable result. Receipts are saved per scenario/project;
older projects need one successful run to establish the new receipt.

## Navigation

Database View opens once site and inventory inputs exist. OPF Production Report
opens once site, OPF and model start are configured. Expit Sequence opens when
the site, 24HR schedule, selected dig circuits and transaction reconciliation
mode are configured. Opening these views loads missing data. Result-only plan
views continue to require saved results.

The Legacy Agent Bridge tab and Site Configuration checkbox are removed.
Continuous Assays belongs to Support; Save assay policy and global Run record
its submission, turn it green and invalidate dependent Workspace tasks.
