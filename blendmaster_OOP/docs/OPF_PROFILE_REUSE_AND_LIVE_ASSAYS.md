# OPF profile reuse and live assay scope

Follow-up to the continuous operations implementation, 14 September 2026.

## Historical profiles

Combined OPF profile preparation is deferred until saved inventory and AMT chunk
restoration is complete. The source/evidence fingerprint is versioned. Its
detached profile cache is included in site checkpoints and shared publications.
An unchanged project can reuse this work after reopening. A selective import can
reuse Support's cache only if it matches the complete merged source inputs;
retained Planner inputs cannot be silently overwritten to make the cache match.

The fingerprint includes source inventories, AMT members and chunks, mappings,
reconciliation policy and historical evidence, OPFs and scenario start. Live assay
offsets do not invalidate historical chemistry. Changed inputs or an older cache
version trigger preparation. Existing files without this cache need one
preparation and a subsequent Save Project before persistent reuse is available.

Read-only verification of `CC_Combined_OPF_Validation.prj` measured:

- Project deserialization: 5.32 seconds.
- Fresh preparation of both OPFs: 937.56 seconds.
- Matching-cache lookup: 2.57 seconds.
- All compared inventory/chunk chemistry and mapped properties matched the saved
  model: 338 inventory records and 19 chunks for each OPF.
- Saved-state normalization and AMT guarding changed none of the fingerprint's
  input fields.
- A separate Qt restore check with a verified cache completed in 171.43 seconds,
  made no profile-build calls, restored all 19 chunks, and retained matching
  fingerprints after both control hydration and capture for saving. Browser
  surfaces and chart-server startup were substituted for this offscreen check
  because embedded Chromium could not initialise on the offscreen platform.

These are component timings, not a complete native application reopening time.
The original project was read without modification.

## Continuous assays

Continuous assay reconciliation is restricted to **Now** mode. Set Time and
disabled policies skip polling before profile lookup, model capture and warehouse
requests. Calculation code also gates overlays and assay-derived time boundaries,
so saved corrections cannot affect a historical replay.

Eligibility requires positive inventory-stockpile or AMT-chunk feed rows covering
the current time in the selected saved optimised plan. A missing plan or missing
active feed evidence withholds reconciliation. Other named plans, manual reports,
future/finished intervals and direct-tip grade blocks are not substituted. Actual
crusher movements and laboratory evidence must still attribute the full assay
window to the exact eligible physical builds; AMT also requires unambiguous chunk
attribution. Inactive components cause withholding rather than reallocating their
assay error to the eligible sources.

The estimator retains the existing Support bounds, dry-mass validation, measured
OPF alignment lags, covariance and causal availability handling. It uses the
historical adjusted-product grades as priors. Calculation-time checks require the
same physical build/prior and current active source scope. Direct-tip events
cannot inherit corrections even if a source name happens to collide.

Polling checks due sites before expensive work, takes compact snapshots directly
from current model attributes without capturing the whole model, checks active
blends before requesting background OPF preparation, and discards asynchronous responses after source, policy,
time-mode or active-blend changes. Missing measured transport-to-assay alignment
lags still withhold observations.

## Results presentation

The Blend Snapshot panel, including its heading and border, is hidden when it
contains no rows. It reappears when the result selection provides rows.

Transport, conveyor, COS and steady-state splitting logic and model setup inputs
are unchanged by this follow-up.

## Validation evidence

All 1,285 regression tests passed in 82.73 seconds on 14 September 2026.

Diagnostics are in the Playground workspace:
`bm_opf_reuse_benchmark.json`, `bm_opf_normalisation_check.json`,
`bm_cached_ui_restore_verified.json`, `bm_snapshot_panel_verified.json`
and `bm_opf_assays_final_full_tests.log`. The real Dash layout and callback
endpoint passed an empty → populated → empty panel check.
Regression coverage includes cache reuse/invalidation and selective imports,
polling gates and stale responses, active stockpile/chunk scope, inactive assay
components, direct-tip exclusion and Set Time calculation behavior.
