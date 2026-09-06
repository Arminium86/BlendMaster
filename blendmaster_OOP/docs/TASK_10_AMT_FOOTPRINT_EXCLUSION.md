# Task 10: Whole-footprint exclusion in AMT setup

Implemented 6 September 2026 under Q49–Q50. Task 11 is not started.

## User workflow

1. Open **AMT Stockpiles**. Every AMT footprint selected in Stockpile Inventories
   has an **Include footprint** checkbox, initially checked.
2. Uncheck a footprint to exclude it. Its setup row stays visible with
   **Excluded** and an audit tooltip. Excluded rows do not require reclaim-rate
   settings or chunks.
3. Use **Submit** to continue with the remaining sources. If all AMT footprints
   are excluded, at least one conventional inventory stockpile must remain
   selected in Stockpile Inventories; otherwise submission is blocked.
4. To restore a footprint, recheck its box, use **Refresh AMT Data from
   Snowflake**, and regenerate its chunks. Restoration does not revive cached
   hexes or old chunk selections.

The control is available in AMT setup without returning to Stockpile
Inventories. Participation controls are populated from selected inventory names
even if the initial AMT query fails. An exclusion applies to the whole footprint
in this scenario, independently of the existing individual-hex exclusions.

## Processing and persistence

- Build requests are formed from included AMT selections. Persisted exclusions
  are honored before a warehouse request. With all footprints excluded, no AMT
  query or hex enrichment runs.
- Excluded footprints are removed before canonical field mapping, grade-stream
  reconciliation, Data Streams review, AMT map data, automatic/manual chunk
  generation and saved-chunk rebuilding.
- Canonical, live-map and solver chunk snapshots are filtered. Database View,
  AMT balances and manual source selection omit the excluded material.
- The solver receives only participating stockpiles and chunks. DataLoader
  also applies the exclusion list defensively, including when an APS destination
  stockpile was added before loading. CaseModeller uses this same filtered
  chunk set for model seeds and depletion reporting.
- Inventory selections remain unchanged in memory so the user can restore a
  footprint. The scenario SQLite opening inventory and AMT tables omit excluded
  footprints so profile reports cannot recreate them from inventory rows.
- Changing inclusion clears the app's generated scheduling report rows. Those
  reports require recalculation for the changed source selection. Input tables
  and unrelated user tables are preserved.

`AMT_footprint_exclusions` persists through scenario capture/activation and
project save/load. New or legacy projects without it default to no exclusions.
Restoring a saved project in Now mode retains its participation decisions;
new site/project resets start without inherited exclusions. Returning to
Stockpile Inventories and deselecting an AMT source also removes its exclusion
setting from the current source selection.

Each active exclusion stores the existing versioned footprint audit schema:
footprint, excluded state, reason, actor label, timestamp, available raw/spatial/
inventory/final tonnes, source-row count and processing eligibility. These are
values observed before exclusion, not available scheduling tonnes. A restoration
retains the last exclusion audit and records its restoration time. This audit
is setup/project state; no excluded-footprint report is introduced.

## Cache and worker behavior

Opening-request, enrichment, evidence-review and Database View signatures
include participation. A retained subset of warehouse rows can still be reused
without refetching. Missing restored footprint rows fail compatibility and
require a new read. The background AMT request captures its start time, and a
callback whose request no longer matches the current selection is discarded.
Map and solver boundaries also filter stale chunk rows independently.

## Validation

- **678 tests passed**, including **17 new Task 10 tests**. The existing pandas
  warnings in CaseModeller and DrawCharts remain unchanged.
- Tests cover exclusion/restore, original inventory and audit preservation,
  retained-cache reuse, query build filtering and frozen timestamps, stale
  callbacks/chunks, skipped enrichment, evidence review, Database View, the
  all-excluded submission rule, defensive solver loading, SQLite persistence,
  report invalidation, JSON/pickle round trips and actual scenario capture.
- Native Qt tests operate the production checkbox callback and table rendering.
  The rendered table was visually inspected with SP1 excluded and SP2 included;
  excluded rows retain their restoration control and have no chunk settings.
- `git diff --check` passes. Tests use synthetic data and temporary SQLite
  files. No warehouse query, production-data write or running-app modification
  was needed for validation.

Restart a running BlendMaster process to load the new control. Changes are
uncommitted; Task 11 remains next.
