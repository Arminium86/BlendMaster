# 11 - Service APIs Persistence and Reporting Contracts

## Current versus proposed

The desktop persists project state and plan-aware SQLite reports. The API below is a proposed engineering contract for the web implementation, not an existing endpoint. Final names and schemas belong in a reviewed OpenAPI specification.

## Proposed command/query surface

| Operation | Proposed interface | Important contract |
| --- | --- | --- |
| List authorized sites | GET /sites | Server filters by principal/site access |
| Read/update model | GET/PATCH /sites/{site}/model | Revision/ETag required for writes |
| Accept input | POST /sites/{site}/inputs | Upload reference, schema, source revision, validation receipt |
| Prepare or plan | POST /sites/{site}/runs | Endpoint, frozen input revision, start, horizon, settings and idempotency key |
| Run status | GET /runs/{run} | Stage, progress, warnings, result revision and failure category |
| Cancel | POST /runs/{run}/cancel | Idempotent; cancelled only after worker termination is acknowledged |
| Compare/merge inputs | POST /sites/{site}/input-merges | Base/local/incoming revisions, selected groups and conflict checks |
| Read reports | GET /plans/{plan}/reports/{report} | Result revision, pagination, filters, units and schema |
| Prepare export | POST /plans/{plan}/exports | Freshness/physical/equipment checks and export artifact receipt |
| Publish approved result | POST /plans/{plan}/publications | Role, review evidence and compare-and-swap current pointer |

Commands that create long work should return an accepted job identifier and a status location. A repeated idempotency key for the same command returns the existing job; a conflicting payload is rejected. Stale revision writes must return a conflict rather than discard someone else's edits.

## Job and snapshot lifecycle

Proposed states are queued, running, cancelling, succeeded, failed and cancelled. Keep calculation outcome separate from plan readiness. Include a monotonic event sequence and timestamps for progress so reconnecting clients can resume.

Workers consume immutable configuration/source snapshots. Store metadata and publication state transactionally; store large evidence/results as versioned artifacts with hashes and row/schema summaries. A worker writes to its own staging area, validates completeness, then marks its result complete. Only a validated complete result can be the publication target.

Avoid returning entire project objects or multi-GB audit frames to the browser. Use pagination, selected columns, server-side aggregates and spatial/timeline tiles where appropriate. Pin every request to a result revision so independently loaded panels remain consistent.

## Existing report contracts

| Report family | Grain/use |
| --- | --- |
| optimised_blend_report / optimisation_plan_blend_report | Selected feed transactions by plan/source/state |
| optimisation_plan_build_report | Physical stockpile build/depletion state |
| optimisation_plan_product_build_report | Product-arrival/build progress and grade evidence |
| manual_plan_blend_report | Independent manual result rows |
| material_destination_plan | Parent grade block and assigned destination |
| destination_primary_assignments / destination_capacity_ledger | Payload allocation and capacity transition |
| two_wp_grade_block_turnover_audit | Prepared source guidance including non-direct-tipped sources |
| continuous_assay_* | Observations, estimator/application evidence |
| closing_rom_stocks_compliance | Physical closing ROM against completed 2WP target period |

Table schemas vary by mode and dynamic properties; inspect SQLiteDatabase, SavedPlanStore and the report producers. Preserve plan type, named plan, OPF/point, source/build and revision scope. Dynamic `source_property_<field>` and custom-constraint fields need schema metadata rather than hard-coded UI assumptions.

## Export and review

Generate operational PDF/XLSX from saved results with the same point/plan selection as the screen. Include selected stream, units, start/end, source revisions, readiness findings and export timestamp. Block stale, physical or equipment-invalid exports according to the existing gates. Volume/quality/destination review findings must remain visible; a produced PDF is not production approval.

Closing ROM compliance compares each planning boundary with the latest completed 2WP closing period at or before it. AMT uses remaining reconciled chunks without a second inventory adjustment. Handle a zero comparison denominator explicitly.

## Legacy migration

The current .prj format is pickle-based and embeds database snapshots. Restrict conversion to approved trusted files in an isolated migration process. Emit declarative configuration, source snapshots and result artifacts, validate schema and conservation, and preserve the original checksum in the migration receipt. Do not expose pickle deserialization to browser uploads.

Separate application release, input schema, model schema, planning semantics, report schema and layout versions. Migrations should reject unknown future versions and preserve raw accepted evidence.

**Evidence:** [database writers](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/database/SQLiteDatabase.py), [saved plan store](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/SavedPlanStore.py), [result views](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/SavedResultViews.py), [readiness](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/PlanReadiness.py), [exporter](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/SpreadsheetReportExporter.py), [migration rules](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/PlanningPersistence.py).

