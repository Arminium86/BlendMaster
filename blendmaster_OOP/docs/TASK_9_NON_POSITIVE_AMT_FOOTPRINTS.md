# Task 9: Non-positive AMT footprint reconciliation

Implemented 6 September 2026 under Q46–Q48. This task corrects tonnage
reconciliation; whole-footprint user exclusion remains Task 10.

## Final tonnage rule

The guard uses the signed sum of the footprint's hex `RAW_WMT`, before
clipping negatives or allocating inventory. `UNATTRIBUTED_MOVEMENT_WMT` does
not participate in that decision.

| Raw hex total | Inventory | Final footprint tonnes |
| --- | --- | --- |
| At or below zero | Any value, including unavailable | Zero in every hex |
| Positive | At or below zero | Zero in every hex |
| Positive | Positive | Reconcile spatially corrected hex tonnes to inventory |
| Positive | Unavailable | Retain spatially corrected AMT tonnes |

For example, raw hexes of −50 and +20 WMT with inventory of 200 WMT now
produce zero final tonnes. The +200 WMT inventory cannot recreate AMT material.
Adding +500 WMT of unattributed movements does not change this outcome.

## Audit and visible behavior

- Raw signed hex values, unattributed movement and original inventory remain
  available. Spatial donor/deficit evidence is retained even when final tonnes
  are zero.
- `RAW_HEX_STOCKPILE_WMT` is the total used by the guard. The legacy
  `RAW_STOCKPILE_WMT` keeps its previous raw-plus-unattributed meaning.
- Each hex carries the versioned `AMT_FOOTPRINT_AUDIT`, including raw,
  spatial, inventory and final WMT, source-row count, outcome, reason and
  processing eligibility. SQLite persists it as `amt_footprint_audit_json`,
  alongside `raw_hex_stockpile_wmt` and `spatial_recon_reason`.
- Outcomes distinguish `zeroed_non_positive_raw`,
  `zeroed_non_positive_inventory`, `retained_inventory_unavailable` and
  ordinary `reconciled` footprints.
- The AMT table's **Raw Signed AMT WMT** now excludes unattributed movement.
  **AMT Total WMT** shows **0 · zeroed** with the reason in its tooltip, and
  the calculated chunk count and size are zero. Inventory remains visible in
  its own column.
- Data Streams shows a zero-tonnage footprint audit instead of substituting
  positive inventory into its evidence-match review. Database View omits the
  zeroed AMT source.

## Cached projects and downstream material

Snapshot repair applies before restoration to SQLite, AMT enrichment and table
preparation. It uses saved raw evidence without a new warehouse query, leaves
positive footprints alone and is idempotent. Missing raw evidence is not
interpreted as a confirmed zero raw total.

The derived enrichment and chunk reconciliation versions change; the warehouse
opening-request cache version stays unchanged. Remaining grade-block lineage
is realigned to zero while inbound lineage evidence is retained.

Zeroed footprints are pruned from the canonical chunks, live map selection and
solver chunk snapshot. Rebuilding saved chunks also discards a chunk when its
known member hexes are all non-positive. Submission does not require impossible
chunks for a zeroed footprint, and fresh chunk generation cannot create them.

The solver loader works on a copy of the selected inventory data. An AMT
stockpile without a positive chunk receives zero opening balance and no mapped
source quantities; it cannot fall back to the inventory balance. AMT stockpiles
with zero available balance generate no reclaim events for the optimizer.
Conventional inventory sources and positive AMT chunks retain their behavior.

## Validation

- Full unittest suite: **661 tests passed**, including **17 new Task 9 tests**.
  The existing pandas warnings in CaseModeller and DrawCharts remain unchanged.
  `git diff --check` passes.
- Regression tests cover negative, zero and mixed-sign raw hexes; positive,
  zero, negative and missing inventory; signed unattributed movements; raw
  audit preservation; idempotent repair; JSON/pickle/SQLite persistence;
  project restoration; lineage alignment; fresh and stale chunks; Data Streams;
  Database View; submission; and solver loading/event generation.
- The former Task 0 tests documenting inventory inflation now assert zero.
  The AMT stream-selection integration fixture now supplies a positive chunk
  instead of relying on the removed inventory fallback.
- The saved Task 7 KAN82 extract contains 226 hexes with raw hex sum
  57,729.268 WMT. Every final hex tonne value is unchanged against the previous
  implementation; final footprint mass remains 5,124.15632 WMT.
- Native Qt table verification uses synthetic data and the production table
  preparation/settings methods, with warehouse, map-server and persistence
  calls disabled. The rendered table was visually inspected: raw −30 WMT,
  inventory 200 WMT, final **0 · zeroed**, and zero calculated chunks. Production
  data and the user's running application are not changed. Database tests use
  temporary SQLite files.

Task 10 is not started. Changes are uncommitted.
