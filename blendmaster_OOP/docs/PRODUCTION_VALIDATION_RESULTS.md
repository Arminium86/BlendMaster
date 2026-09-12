# Production-readiness validation — 12–13 September 2026

The implementation preserves the existing blending policies and adds the agreed
workflow, role separation, import preservation, preparation contracts, dependency
checks, final-plan checks and UI changes. AWS hosting and authenticated web roles
remain production integration work. No commit or deployment was made.

## Automated evidence

- Baseline: 1,099 tests.
- Final regression suite: **1,168 tests passed in 51.022 seconds**.
- Additional native application/save/restore/export fixture: **passed in 8.529
  seconds**. Real solver paths completed for single-point, multiple-point and
  combined-OPF configurations. This fixture replaces Chromium charts and stubs
  warehouse hydration; the live checks below cover actual chart/warehouse use.
- The native fixture also exercised conveyor/COS flow, point-specific exports,
  project persistence and 20 timeline updates across 12,000 feed rows.
- Regression coverage includes failed replacement rollback; preserved imported
  selections and target overrides; role checks; worker/database ownership;
  independent manual-plan freshness; Calendar rate limits after ratio rounding;
  fractional-second optimised-to-manual transfer; physical Gantt ratios; snapshot
  occurrence selection; four-decimal assays; atomic background project saving;
  and background blend-plan exports.

Logs are in the user's Playground directory:
`BlendMaster_implementation_full18.log` and
`BlendMaster_native_delivery_final3.log`.

## Real project and workflow

The original `blendmaster_20260911_2005.prj` was retained at 1,308,199,627 bytes,
with its 11 September 2026 20:05:24 modification time. All acceptance work used
separate sessions and output files. The project is a CC single-point model with
OPF02 / OPF02_PC, three periods, and the saved start of 18 August 2026 19:51:49.

The accepted model contains 338 inventory entries, 1,702 AMT hexes and 19 chunks.
The source files include the site's HI cycles, 2WP and 24HR CSVs. Closing balance
was unconfigured and optional. No substitute closing-balance evidence was created.

The earlier complete agent handoff (`BlendMaster_agent_handoff_v2`) finished all
11 stages in **333.53 seconds**, using accepted input and reconciliation evidence.
It returned `plan_requires_review`, with no failed stage. A cold/uncached run took
975.41 seconds; these timings have different cache conditions and are not a
controlled before/after performance comparison.

The complete site handoff (`BlendMaster_agent_handoff_v3`, run
`7fcf383e-df93-46c2-9296-1d23e5fa027e`) completed successfully with the same
`plan_requires_review` outcome. Its 11 workflow stages took **300.12 seconds**;
total process time including project loading and checkpoint writing was
**380.81 seconds (6 minutes 21 seconds)**. No stage failed. The final run's longest
measured event-loop gap was **9.02 seconds**, during reconciliation mapping and
fingerprinting. Most long operations ran in workers with progress and input locks.

## Live screen checks completed

- Planner sees Workspace and Views. Support sees the additional setup area.
  Site mode, OPFs, operating crushers and technical configuration are separated
  from the planner's site/start/period controls.
- Guidance browse controls, dig circuits and movement rules are in Workspace;
  technical selections persist in Guidance Settings. Single-point Crusher
  Contribution remains available because target scaling still uses it.
- Inventories retain Use/AMT choices. Grade Reconciliation is separated from
  technical streams. AMT maps, targets, expit and destination refreshes were
  exercised with the saved site and Snowflake connections.
- Calendar displays progress and holds input controls while work is active.
- The optimised Gantt renders, hides its old transaction box, shows assay
  precision and returns the selected state's four snapshot rows.
- Manual crusher rate 6,001 was rejected against Calendar 6,000; 4,000 was
  accepted by the dashboard. Exact prepopulation transferred eight states,
  seven definitions and 18,405.35 WMT direct tip. Dashboard and sequence Submit
  both passed. Manual physical feed was 122,819.923596 WMT versus optimiser
  122,819.923603 WMT; the difference is under 0.00001 WMT. Reclaim output remained
  within Calendar 2,000 t/h, allowing only the existing floating-point tolerance.
- The mode-specific Blend Plan displays separate calculation, freshness,
  inventory, equipment, target, quality and destination outcomes. Backup
  destination selection was exercised without the former combo-box freeze.
- Material Destination Plan's five child views were inspected, including
  assignments, payload/capacity evidence, transitions and movements.
- Database View loaded 477 sources. OPF Production Report showed the explicit
  no-data result for FB and 290 SS records. Optimised and manual Grade Profiles
  rendered. Material Flow's six child views were inspected.
- Build and Depletion Profiles paginates 474 sources: page 1 shows 12 and page 40
  shows sources 469–474. Both pages and their charts were checked live.
- Closing ROM Stocks Compliance correctly reports the missing optional workbook.
- Support checks covered Site Model Settings, Guidance Settings, Define Fields,
  Map Fields, technical Data Streams, Solver Configuration, Conveyors & COS,
  Database Reports, Site Automation and Decision Diagnostics. Multi Feed Setup
  is disabled in this single-point model; Legacy Agent Bridge is disabled while
  its legacy feature is off.
- Normal close → Yes saved a separate 1,345,763,837-byte project and closed only
  after the background write finished. The original project was unchanged.
- The final persistence pass restored the saved manual sequence table and Gantt
  without dashboard submission. Destination Reconciliation reused its saved
  23:18:18 AWST fetch with 1,182 qualifying movements without a warehouse query.
  Freshness and persistence now use the same current recipe inputs, including
  unsubmitted recipe edits; numeric display formatting retains exact ratios.
- The final 1,402,963,075-byte handoff reopened in 74.34 seconds. Both optimiser
  and manual recorded input revisions matched the reopened model, with no
  unhandled application errors. The final project is
  `Playground/BlendMaster_validated_handoff_final.prj`; it was saved through the
  normal close → Yes flow. Raw saved-state inspection also confirmed manual
  revision equality before UI hydration.

## Export evidence and outstanding business review

The first live optimised export contained 24 XLSX sheets, including 16,634
reconciliation-factor rows, and a six-page PDF. Raw phosphorus values retained
their numeric precision. The export review exposed two presentation issues that
are fixed in the final code: four-decimal assay formatting and Gantt ratios
derived from actual physical tonnes. Large XLSX writes now run in a worker.
The subsequent 24-sheet workbook and all six PDF pages were inspected. Grades
have four decimal places (for example P 0.0522); backup and review notices persist.
During both exports the session's maximum event-loop gap remained 4.48 seconds,
which came from project restore. A final detail-sheet check also aligned saved
legacy ratios with the physical recipe and retained four-decimal ratio formatting.

Final exports from the reopened handoff are `BlendMaster_final_optimised.xlsx`
(24 sheets, 5,517,908 bytes), `BlendMaster_final_manual.xlsx` (20 sheets,
5,488,100 bytes) and matching PDFs in Playground. Both workbooks passed
calculation, freshness, physical inventory and equipment checks; targets,
quality and destinations retained their review status. Every operational detail
ratio matches source tonnes divided by the state total, and the eight-state
totals remain 122,819.923603 WMT optimised / 122,819.923596 WMT manual. Source
audit sheets preserve their underlying model evidence. Both workbooks retain
16,634 reconciliation-factor rows and four-decimal assay/ratio formats.
The final export session's maximum event-loop gap stayed at the 8.81 seconds
measured during project restore; exports introduced no larger pause.
All six optimised PDF pages and eight manual PDF pages were rendered and
visually inspected, including Gantt cards, summary tables, multi-page detail
tables and continuation columns. No clipping or overlap was found. The final
PDFs are 18,306 bytes optimised and 961,618 bytes manual. The reproducible
workbook/PDF receipt is `Playground/BlendMaster_final_export_validation.json`.

The accepted model's output requires planner review under the existing policies:

- SS Build 1 has approximately 70,785.3 WMT remaining against its optimiser
  target; the manually replayed plan reports 70,815.1 WMT under the existing
  manual product/grade calculation path. Physical feed tonnes agree as above.
- Soft Fe/Si/Al quality breaches remain visible.
- Some destinations are unresolved or outside the planning horizon.
- Expected replacement source deliveries have not arrived; the accepted previous
  versions remain usable under the configured retention policy.

Calculation completion is therefore not represented as business approval.
Physical inventory, equipment and freshness checks passed on the regenerated
optimised plan. Review notices remain part of the exported handoff.

## Release checks for the site owner

Use [the UI acceptance checklist](PRODUCTION_UI_ACCEPTANCE.md) for release sign-off
and [the operations guide](SITE_WORKFLOW_OPERATIONS.md) for roles, site contracts
and the CLI. The real site walkthrough used the supplied single-point model;
multiple-point and combined-OPF modes have automated fixture coverage and still
need acceptance on each site's actual production model.

Confirm the production timezone, delivery locations, closing-balance requirement
and enabled schedule. Confirm authenticated server permissions, credentials,
external scheduling and the execution host when moving the PoC to the web.
Live reconciliation and manual destination publication still have short
synchronous phases. The complete site run's longest event-loop gap was 9.02
seconds; the final manual restore/prepopulation/submission pass measured 8.72
seconds. These pauses remain a production performance consideration.

## 13 September follow-up: view availability and automatic charts

Expit Sequence now belongs to Views. Expit and Destination Reconciliation are
disabled until their required site, schedule and selection/inventory inputs are
available. Grade Profiles and its Optimised/Manual children follow actual report
rows in the active database. Saved tab flags and delayed results from a previous
site cannot enable an unavailable view.

Chart Load/Update buttons were removed. Available charts load on entry and refresh
after prepared data, selected plans or generated reports change. Local service
startup is checked in a background worker; a failed connection can be retried by
reopening the page. Optimised grade layouts read committed reports on each load.
Matching Expit evidence is reused until its inputs change. Hidden navigation
groups do not trigger page entry work or warehouse refreshes.

Validation completed:

- Full regression suite: **1,180 tests passed in 47.327 seconds**.
- Dash HTTP layout and callback checks: updated grades and a second database
  produce the current chart values without calling the legacy refresh endpoint.
- Native Qt checks: fresh-project disabled states, prepared-input availability,
  independent grade pages, automatic entry loading, one refresh per changed
  result, no remaining legacy buttons and reuse of the AMT panel all passed.
  Navigation screenshots were inspected. Chromium was replaced by placeholders
  in this isolated native check; Dash HTTP rendering was tested separately.
- `git diff --check` passed. The running user application was left in place;
  restart it to load the updated code and use the revised UI acceptance checklist.

Evidence is in the user's Playground directory: `BlendMaster_view_flow_full_final.log`,
`BlendMaster_view_flow_native.json`, `BlendMaster_view_flow_empty.png` and
`BlendMaster_view_flow_optimised.png`. The native fixture is `bm_view_flow_native.py`.
