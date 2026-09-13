# Continuous operations implementation — 13 September 2026

User-authorised batch from the pasted implementation request. Starting commit:
`ff22949` on `main_BlendMaster_ultimate_prod_streams`; working tree was clean.
Keep the original supplied project intact and use a separate combined-OPF copy
for live acceptance. Changes to business algorithms are limited to the requested
online reconciliation and route timing behaviour. Calendar equipment limits,
shared physical inventory, independent OPF chemistry and existing quality policies
remain authoritative.

## Work ledger

- [x] Baseline regression suite: 1,185 tests passed in 43.391 seconds.
- [x] Continuous, bounded assay reconciliation for active stockpiles/chunks;
      Support settings, automatic refresh/application, persistence and audit.
- [x] Planner noise policy and selected-stream-only operational views, including
      the AMT chunk sequence; retain full Database View/Database Reports evidence.
- [x] Rename destination Progress to Review Destinations; omit technical build
      order and movement identifiers from visible operational tables.
- [x] Named Support solver presets selectable from Planner Decision Levers.
- [x] Manual product-build grade charts and HQL/LQL/Target overlays in both modes.
- [x] Optimised/manual selection for build/depletion profiles and material flow.
- [x] 2WP route/truck-based travel, spotting and dump times; destination-sensitive
      ETAs with the exact user-specified source/material fallback hierarchy.
- [x] Schedule every enabled site contract, publish an atomic shared project
      after the batch, and use stable scenario-based project names.
- [x] Detect upstream project replacements; let Planner selectively merge
      prepared inputs while preserving unsaved planning edits and invalidating
      affected results. Keep Prepare Inputs available to Planner.
- [x] Automated regression and isolated application integration, including
      persistence, conflict imports, scheduler failure/cancellation and both result types.
- [x] Load the supplied project, configure Combined OPF with all available
      crushers and reasonable conveyor/COS inputs through computer use.
- [x] Run the combined-OPF UI acceptance pass, fix discovered defects and deliver
      the saved model, measured evidence and remaining limits.

## Completed acceptance — 13 September 2026, 23:00 AWST

The native combined solve completed all three planning periods through
20 August 2026, 06:00 with both OPFs and OPF01_PC, HAL_PC and OPF02_PC.
Optimised and copied manual reports each contain 636 feed rows over 211 steady
states. Equipment checks passed; the largest conveyor/COS mass-balance error
was 6.9e-7 WMT. Native grade, build/depletion and material-flow pages rendered
in both modes, including the manual closing horizon and retained FIFO contents.

The separate validation model uses the user-approved soft grade targets and
two COS zones. Its remaining product volumes and soft quality breaches are
reported in Blend Plan; completing the calculation does not mean that production
targets were attained. The original hard prepared model is preserved.

Native selective import applied five prepared groups while Destination activity
remained unselected. The fresh unsaved contingency edit from 13 to 14 survived,
as did target policies, all 36 selected Database View fields, destination
activity, manual planning work and Gantt inputs. The checkpoint was saved and
read back successfully (`Playground/bm_native_planner_merge_verified.json`).
Changed evidence correctly marks dependent manual results for recalculation.

The final native walkthrough also found and corrected these defects:

- Combined-OPF display renumbering could duplicate a refreshed product target.
  Target identity now follows its OPF, crusher, brand and actual build period,
  preserving the existing target policy. Ambiguous duplicates require review.
- Import could start expensive OPF profile preparation on the UI thread.
  Preparation now finishes in the background before the import releases controls.
- Save was disabled when guidance inputs were incomplete. Planner can now save
  a working checkpoint independently of preparation readiness.
- Manual SQL publication copied large attached audit frames through pandas
  column operations. Each table now receives detached data while the associated
  reports still publish atomically.
- Grade legends show readable OPF/build labels with space below the time axis;
  rate profiles include zero so numerical rounding does not exaggerate changes.

All 1,269 tests passed in 58.118 seconds (`bm_final_database_full_tests.log`),
and `git diff --check` passed. Latest native chart rendering is verified in both
modes. Real-data haulage checks matched 2,000 assignment rows, including 786
destination changes, against the actual 2WP route/truck/timing rows. ETAs agree
within the saved loading timestamps' whole-second precision.

The final reopen also found and fixed automatic calculation when loading a
Calendar checkpoint without current results, and a missing manual Gantt legend
when opening Manual Blend Sequence before Blend Plan. Project loading now waits
for an explicit calculation, and the chart derives its legend from saved recipes.
Assay requests now exclude large reconciliation audits: the real combined model
captures in 0.923 seconds and serializes to 929,955 bytes with identical source
catalogs. Preparation now checks the current opening-history request/version
before assays and publication, preventing legacy opening records from being
handed on as prepared inputs.

The final scheduled publication completed at 22:07 AWST. Read-back verified
exactly two targets with every original editable value and policy preserved,
unchanged transport settings, 19 AMT chunks and 27 exact opening movements.
The enabled site completed with `inputs_ready`; the stable shared file is
`C:\BlendMaster\SharedProjects\CC_Combined_OPF.prj` (1,586,610,328 bytes).
Evidence: `Playground/bm_shared_publication_final_verified.json`. The temporary
publication session was paused and closed without saving over the original hard
model; its SHA256 remains
`1FE2333AB6E79962AF2A15876BF0205C9499417399B5441F1036B16DBAD5368E`.

Manual Blend Sequence now reopens without the missing-legend crash. The native
allocation copy completed within the 23.225-second observation window. Working
set peaked at 6,632,828,928 bytes, increasing by 371,159,040 bytes from its
pre-copy baseline; the later project save is excluded from those measurements.
Both resulting reports passed equipment, grade-trace and transport conservation
checks. The validation project was saved natively at 22:28:17 AWST
(2,348,557,219 bytes). Its embedded database passed SQLite integrity checks and
retains complete optimised/manual reports with 636 rows and 211 steady states.
Evidence: `bm_native_copy_resource_verified.json`,
`bm_final_native_manual_validation.json`, `bm_final_validation_project_verified.json`.

Planner's native Save Project control is enabled independently of guidance
readiness and successfully saved the restored contingency value of 13. Read-back
caught Database View silently adding 16 new reconciliation audit fields to the
explicit 36-field selection. Refresh now retains an explicit selection exactly,
and the field dialog retains unavailable choices while the user edits available
fields. Every stored stream/brand grade is available in that dialog even with
a minimal field registry. Three regressions cover schema changes, complete
grade availability and real Qt field-dialog acceptance. Only the 16 automatic
additions were removed from the Planner checkpoint; all other saved work remains.

The final native reopen passed. Database View showed the original 36 selected
fields, with 119 available fields and per-brand product grades visible in the
searchable picker. Its 19 AMT chunks and 343 APS blocks rendered successfully.
The Planner project was saved natively at 22:59:59 AWST (1,569,327,651 bytes).
Final read-back verified contingency 13, exact preservation of the 36 field
choices, target values/policies, destination activity, manual planning work and
Gantt inputs. It retains 19 AMT chunks, 27 opening movements and the upstream
shared-project subscription. Changed prepared inputs correctly retain the
`inputs_updated` outcome until Planner explicitly recalculates.
Evidence: `bm_native_planner_final_verified.json` and
`bm_real_database_preferences_verified.json`. The original hard-model checksum
was checked again after the final saves and remains unchanged.

### Saved handoff files

| Purpose | Project |
| --- | --- |
| Preserved original hard prepared model | `C:\BlendMaster\blendmaster_OOP\CC_Combined_OPF.prj` |
| Planner checkpoint with selective imports and preserved work | `C:\BlendMaster\blendmaster_OOP\CC_Combined_OPF_Planner.prj` |
| Completed separate soft-target validation | `C:\BlendMaster\blendmaster_OOP\CC_Combined_OPF_Validation.prj` |
| Verified scheduled Support publication | `C:\BlendMaster\SharedProjects\CC_Combined_OPF.prj` |

The historical combined project takes approximately 20 minutes to reopen on
this machine while independent OPF reconciliation profiles are prepared. Its
background workers remain active and the window remains responsive. Live assay
application requires Support to supply measured OPF feed-to-assay alignment
lags; these were not invented for the historical acceptance case. The desktop
schedule runs while a Support session is open. Company screen-lock restrictions
still apply; no keep-awake helper is running.

## Confirmed decisions and implementation boundaries

- User confirmed automatic application of validated live assay updates within
  Support-configured bounds. Updates affect future calculations; saved completed
  result snapshots retain the grades with which they were calculated.
- User confirmed the configurable shared folder default:
  `C:\BlendMaster\SharedProjects`.
- A mixed assay is a constraint on the contributing blend. Retain estimator
  uncertainty and source correlations; do not claim that a fixed blend uniquely
  identifies each source's true grade. Use physical contribution evidence,
  correct assay/product basis, transport timing, unique observations, bounded
  adjustments, explicit provenance and rejection reasons.
- The haulage lookup searches the assigned destination, same material in
  flitch → blast → bench → stage → pit → mine order, then another ore material
  through the same hierarchy, then the 24HR values. At each route match prefer
  the 24HR truck model; otherwise use the first matching 2WP row. Loading remains
  payload / loader rate from 24HR. Every destination change recomputes ETA.
- Shared-project imports are explicit, selective and transactional. Planner
  choices, manual recipes, Calendar edits, target overrides and unsaved work must
  survive unless the user selects a specific conflicting incoming value.
- Computer-use runtime was initialized at the start. Use its native window API
  for the live walkthrough; do not alter company screen-lock/security settings.

## Evidence

Baseline log: `Playground/BlendMaster_batch2_baseline.log`.
Further evidence and exact results are recorded as checks complete.

## Implemented behavior

`classes/ContinuousAssays.py` implements a linear Kalman measurement update on
source-grade offsets, retaining the complete covariance. A constant mixed blend
therefore retains uncertainty about its individual sources. The method follows
the Kalman/RLS relationship described by [Lai and Bernstein (2024)](https://arxiv.org/abs/2404.10914).
The use of covariance when updating resource estimates from downstream material
measurements is also discussed by [Prior et al. (2021)](https://link.springer.com/article/10.1007/s11004-020-09874-1).
These references inform the method; the default bounds are configurable product
choices, not values calibrated from those papers.

Support > Continuous Assays exposes enablement, polling, age/mass checks, prior
and assay uncertainty, innovation gates, per-update/total offset bounds and
per-OPF transport alignment lags. Five minutes is the default polling interval.
Prepare Inputs also checks current assays before its validation stage completes,
so a scheduled shared publication includes the accepted evidence available at
preparation time. An unavailable warehouse retains prior accepted evidence;
age, policy and source-identity checks still control whether it can be applied.

The production assay query is the OPF Production Report's existing service.
Its five-minute production rows are collapsed by laboratory sample timestamp
before DMT aggregation; repeated warehouse refresh watermarks cannot add another
observation or shift an unchanged correction's effective time. A substantive
assay revision replaces the original observation and replays the estimator.
Warehouse OPF spellings and unambiguous site-prefixed product brands are matched
to the configured model. Actual crusher feeds use the existing primary-movement
warehouse, with exact physical source-build identity and mapped product dry yield.

All actual sources in an observation must be attributable. Unknown direct tips,
missing quantities, ambiguous AMT chunks, unmatched brands, incomplete/future
samples, overlapping sample windows and mass/bound violations are withheld with
reasons. AMT requires an actual matching inventory build plus one saved-plan
chunk covering the feed window. With conveyor/COS enabled, Support must supply
a validated feed-to-assay lag per OPF. The implementation does not infer a
source's grade uniquely from a fixed mixture or invent its transport residence.

Corrections affect adjusted-product grades and their declared aliases in detached
calculation records. Input priors and completed report snapshots remain intact.
Fresh builds and changed prior chemistry invalidate the old registry, even if a
new build has numerically identical grades. New inbound stockpile material also
disables the old correction within that calculation. Optimised steady states
split at assay availability times. A fixed manual sequence imported from an old
solve must be copied from a recalculated solve when a new assay falls inside one
of its fixed states. Support can inspect persisted `continuous_assay_*` tables in
Database Reports; the Planner sees a short accepted-update count.

Manual feed, product, physical profiles and FIFO reports publish together in one
SQLite transaction. Manual transport uses the existing Conveyor/COS engine and
checks crusher and rehandle service capacities. Direct-tip input is held until
its grouped payloads are available; a manual allocation that cannot then fit is
rejected before publication. Both grade pages show product builds with their
saved HQL/LQL/Target limits. Build/depletion and Material Flow select the result
type and named plan explicitly. Combined-mode manual handoff is an independent
copy of the selected current optimised allocations, including product arrivals
and physical/FIFO state. It is reviewed per tipping point; the combined manual
view does not introduce an allocation editor.

Haul timing retains a frozen route/truck lookup with every payload. Loading stays
on the 24HR payload/rate basis. The assigned destination selects the 2WP travel,
spotting and dump components, following the confirmed material hierarchy and
truck preference. Final capacity allocation replays against revised ETA order
and plan-window membership; a routing/order cycle stops publication explicitly.
The 24HR fallback and selection provenance remain in database evidence.

Shared projects default to `C:\BlendMaster\SharedProjects`. A batch attempts all
enabled site contracts, honours retry/cancel outcomes, and atomically replaces
one stable project file only after every enabled contract succeeds. Planner
Save Project uses a stable model/scenario filename in the application working
folder. Its upstream subscription remains separate from its local project save.
The Planner replacement notice opens a three-way, selectable group comparison;
local conflicts start unchecked. The merge clones databases into a new session
before switching them, retains local plan/Calendar/target/recipe work, and marks
affected results for recalculation. An incompatible evidence basis requires the
corresponding planning-window/site/field-policy groups to be selected together.

## Validation and remaining acceptance

- Full automated suite: 1,240 tests passed in 56.601 seconds, including native
  worker lifetime and permission-wrapped button regressions added during UI
  acceptance. The latest complete result is recorded in
  `Playground/BlendMaster_batch2_serial_handoff_full_tests.log`.
- Isolated Qt application integration restored the supplied project at its saved
  18 August 2026 planning start. It saved a Support policy and named preset,
  copied 22 feed rows into a separate manual plan, exercised manual product grade
  and depletion chart generation, and selected Combined OPF with all three
  available crushers: OPF01_PC, HAL_PC, OPF02_PC. No application exceptions or
  error dialogs were recorded. Chromium panes were placeholders in this fixture;
  this is supplemental automation, not native computer-use acceptance.
- Evidence: `Playground/bm_batch2_integration.py`,
  `Playground/BlendMaster_batch2_integration.json` and its log; focused assay,
  route, shared-project, scheduler and manual-transport tests live under `tests/`.
- Read-only warehouse verification retrieved 1,157 product rows for 18–20 August
  and 108 actual crusher movements for a six-hour sample. It verified the real
  repeated laboratory-sample grain, OPF/brand spelling and actual feed columns.
  No production estimates or source project files were changed by these checks.
- `git diff --check` passed; changes remain uncommitted.
- Native acceptance resumed after the reported shutdown. The original project
  loaded successfully through the Windows file picker, retaining its saved
  planning start. Support submitted Combined OPF with CC OPF01 and CC OPF02 and
  all three crushers (OPF01_PC, HAL_PC, OPF02_PC). Guidance and the existing
  stockpile/AMT selections were submitted again; the AMT warehouse refresh
  completed successfully with 1,702 result rows.
- The running acceptance session has OPF01_PC and OPF02_PC transport enabled:
  250 WMT conveyor, 1,000 WMT COS, 10 COS chunks, 200 WMT rehandle payload,
  30 seconds spot and 30 seconds dump. HAL_PC passes through without additional
  conveyor/COS storage. Calendar was reviewed and its 1,000 t/h rate and reclaim
  limit per crusher retained as conservative acceptance-model assumptions.
  Product quality limits come from Product Targets.
- Native inspection exposed a misleading configuration confirmation that named
  only the first crusher. The message now lists all configured crushers. This
  correction and its legacy single-crusher fallback passed the regression suite
  and are loaded in the current application.
- The original `blendmaster_20260913_005351_432897.prj` is intact. A separate
  `C:\BlendMaster\blendmaster_OOP\CC_Combined_OPF.prj` recovery project was saved
  through the native UI (latest save: 1,257,022,732 bytes). Read-back verified both OPFs, all
  three crushers, transport settings, 10 selected inventory sources, 81 field
  mappings and the stable shared project name/folder. This intermediate save
  precedes AMT chunk regeneration and the combined solve. It now also includes
  explicit RCH-to-OPF01_PC and RCH-to-HAL_PC movement permissions, the named
  `Combined OPF baseline` solver preset, automatic assay bounds, and 11 opening
  transport movement records with a fresh status and no history warnings.
  Read-back evidence is in `Playground/BlendMaster_batch2_configured_inspect.json`.
- The user restored the minimized window and native input preparation resumed.
  Preparation then exposed a native Qt/SIP deadlock: the GUI waited for Qt's
  connection mutex while a worker QObject destructor waited for the Python GIL.
  Background jobs now execute through a QThread whose QObject affinity remains
  on the GUI thread; results are delivered after the thread finishes, and all
  Qt objects are disposed on the GUI thread. Fifty-nine focused checks passed
  in 3.911 seconds, including 100 real chained progress dialogs, failure paths,
  stale-site delivery and workflow/scheduler regression checks. Native stack
  evidence is in `Playground/BlendMaster_batch2_native_stall_native.txt`; checks
  are in `Playground/BlendMaster_batch2_worker_tests.log`. The frozen application
  was restarted from the saved combined recovery project.
  Native Multi-feed Submit also exposed a permission decorator that forwarded
  Qt's clicked(bool) argument into no-argument handlers, terminating the app.
  The decorator now preserves no-argument handler signatures while retaining
  role checks and explicit payload forwarding. Ten focused Qt/permission checks
  passed, followed by the complete 1,230-test suite. Native resubmission of the
  routes now succeeds; the updated recovery project was saved successfully.
  Preparation then passed the former deadlock and generated AMT chunks. Native
  stack samples exposed lengthy independent-OPF source calculations on the GUI
  thread during reconciliation and chunk submission. Those two handoffs now
  prepare profiles in background jobs, reject changed snapshots and publish
  only a matching cache before continuing. The complete 1,233-test suite passed;
  the application was restarted with this responsiveness fix from the configured
  recovery project. Stack evidence is in the `BlendMaster_batch2_recon_progress`
  and `BlendMaster_batch2_amt_progress_stack` files in Playground.
  Chart services also used fixed ports across processes. Each chart now binds
  its own localhost port before its view connects, allowing Support and Planner
  to run together without showing each other's data. Real Dash layout and
  callback isolation checks passed, along with the complete suite. A Planner
  session is being opened against an initial shared copy at
  `C:\BlendMaster\SharedProjects\CC_Combined_OPF.prj` for native replacement and
  merge checks; this copy is still the pre-preparation recovery model.
  Native Planner loading exposed an empty preset dropdown even though the saved
  library was present. Solver input restoration now refreshes the dropdown
  directly, covering Load Project as well as ordinary sidebar navigation. Six
  focused checks passed, including a real Qt Planner control that gains a
  restored preset and removes it when another project has an empty library.
  Native reloading verified that `Combined OPF baseline` appears immediately
  and applies successfully. The Planner inventory table shows five selected
  grade columns, and Destination Reconciliation shows `Review Destinations`.
  Opening Database View on the pre-preparation project then exposed an uncaught
  missing-factor error. That read-only path now permits pending reconciliation
  factors, retaining saved chunk grades until evidence arrives. Production
  Report context now collects every tipping point's OPF within a combined site.
  All 150 focused navigation, report and reconciliation tests passed in 10.976
  seconds. Planner was restarted with these fixes for continued native checks.
  Native checks now confirm both OPFs plus the combined production-report
  selection, and Database View opens and applies all 36 available report fields.
  Support completed Prepare Inputs and saved the prepared recovery project at
  17:12:46 AWST (1,400,973,740 bytes, 19 AMT chunks). Read-back is recorded in
  `Playground/BlendMaster_batch2_prepared_inspect.json`. Calendar Submit started
  the combined solve using all three configured crushers. The shared copy has
  not yet been replaced; Planner has an unsaved contingency option limit of 13
  for the forthcoming merge acceptance check.
  The first combined solve stopped on HAL01_RP01_0301 opening tonnes. The OPF
  profile retained the warehouse's uppercase BALANCE field, but validation read
  only lowercase balance and treated it as zero. Validation now accepts both
  representations and still rejects genuine balance differences, including an
  explicit zero physical balance. Seven OPF integration checks, 25 expit checks,
  68 property/route checks and the complete suite passed. Payload field-kind
  classification now caches immutable per-handler declarations, avoiding repeated
  scans of every combined-OPF field for every payload.
  Native restoration also exposed source-profile rebuilding on the GUI thread.
  Restored state now queues the existing background profile worker before
  completing the UI setup; a failed profile releases the pending load state.
  All 45 focused profile/workflow/persistence checks passed in 3.417 seconds
  (`Playground/BlendMaster_batch2_restore_profile_tests.log`). The restarted
  Support process is rebuilding the prepared recovery project's OPF profiles.
  A second native stack sample showed Calendar proceeding while AMT profile
  publication was still running. AMT submission now has explicit completion and
  error callbacks; project restoration waits for publication before entering
  Calendar. This prevents concurrent duplicate profile calculations. All 147
  focused restore, screen-flow, AMT and workflow checks passed in 1.792 seconds
  (`Playground/BlendMaster_batch2_restore_handoff_tests.log`). The stale Support
  instance was restarted with both fixes; Planner remains open with its local
  contingency limit of 13 and its 36-field Database View selection. Native
  window switching and the loading progress dialog are responsive again.
  A completed combined solve and full native UI acceptance have **not** been
  delivered yet. Continue optimisation, manual result views and scheduled
  publication/Planner selective-import acceptance.
- A temporary display power-request helper was attempted at the user's request.
  Windows script policy rejected it. No power or security settings were changed,
  and no keep-awake request is active.

Native acceptance continuation, 18:48 AWST:

- The hard prepared recovery was saved natively at 18:11:55 (1,583,969,328 bytes).
  Its original hard phosphorus limit is infeasible for available selected sources.
  The user approved a separate `CC_Combined_OPF_Validation.prj` with soft grade
  targets; the original dated project and hard recovery remain unchanged.
- The earlier 11-record opening-history check was invalid: a loose destination
  prefix had admitted stockpile dumps. Exact physical hopper matching now covers
  direct-feed and rehandle subclasses. Rehandles use stockpile-build snapshots
  as of each movement, inventory field mappings and actual-mass scaling.
  History version 2 refreshes prior saved evidence. A bounded warehouse query
  returned 27 real movements for the enabled conveyors. Continuous assay feed
  evidence shares this destination filter.
- Transport lanes may idle when opening queues occupy their admission capacity;
  their minimum stockpile count and contribution rules still apply to positive
  new feed. All 1,246 tests passed after this correction.
- Exact-input validation then found a sub-microsecond COS fill remainder. Chunk
  sealing now shares the solver's one-gram precision; final rounding remnants
  are included in arrivals to preserve mass. Bulk property operations also
  compile declarations once per operation, avoiding repeated report-time scans
  while preserving declaration overrides and later edits. These latest changes
  passed focused validation. Subsequent runs required 1e-4 WMT sealing tolerance
  for accumulated solver rounding (actual chunk mass is retained). Balance
  updates now process only the current delivery window and avoid copying full
  schedule audit attributes into each transient row. All 1,249 tests passed in
  51.716 seconds; normal `git diff --check` passed. The exact-data validation has
  passed its former stopping points. Native acceptance remains open.

Native acceptance continuation, 19:34 AWST:

- The two-zone validation exposed a nearly depleted COS chunk retaining about
  1.8e-6 WMT and creating a 6.5-microsecond solver interval. Final reclaim and
  conveyor departure now consume rounding remnants within the same 1e-4 WMT
  tolerance used for sealing, with all actual mass retained in arrivals. The
  regression covers a final depletion boundary and exact conservation.
- A remaining bulk-scaling path now compiles property declarations once before
  iterating properties. All 1,250 tests passed in 55.642 seconds, and the normal
  diff whitespace check passed (`Playground/bm_final_mass_full_tests.log`).
- Native Support verified 27 actual opening movements and saved the separate
  soft-target validation model at 19:30:01 (1,617,069,984 bytes), with two COS
  zones at each enabled conveyor. Its saved run is explicitly marked aborted;
  it is a recovery checkpoint, not an accepted completed plan. The original
  hard prepared model retains its 18:11:55 timestamp and targets.
- Support was restarted with the tested code and is restoring that validation
  checkpoint. The exact-input independent run has passed into the second
  planning period. Full completion, result charts, scheduled publication and
  Planner selective-import checks are still outstanding.

- At steady state 130, the longer run also exposed a nanosecond stockpile
  turnover boundary being truncated by the microsecond planning clock. Turnover
  boundaries now round up to the clock's precision, leaving the source timestamp
  untouched. A regression reproduced the original division by zero and verifies
  two consecutive real solves and conserved stockpile mass. All 1,251 tests
  passed in 55.135 seconds (`bm_turnover_full_tests.log`); the normal repository
  diff check passed. The full-data run and latest native session are being rerun.

Independent full-horizon acceptance, 19:52 AWST:

- The exact prepared real-input run completed through 20 August 2026, 06:00,
  covering all three periods, both OPFs and all three crushers. The saved plan
  is explicitly `complete`, with 636 feed rows over 211 steady states and no
  partial-plan restoration. The separate validation assumptions remain soft
  grade targets and two COS zones; the original hard model is unchanged.
- Read-only report validation passed for product builds, five analyte charts
  (11 traces each, including both OPF build curves and quality lines), and
  conveyor conservation. Maximum closing balance error was 6.9e-7 WMT.
  Evidence: `Playground/bm_combined_run_outcome.json`,
  `Playground/bm_full_horizon_validation.json` and
  `Playground/bm_combined_validation_turnover.log`.
- Native acceptance remains in progress: the latest Support instance is
  restoring the validation checkpoint; a separate Support session has restored
  the original hard OPF01 target and ten-zone transport assumptions for the
  stable shared publication. Planner retains unsaved contingency limit 13.

Native full-horizon and publication acceptance, 13 September 2026, 21:00 AWST:

- The native combined calculation completed all three periods through
  20 August 2026, 06:00, with both OPFs and all three crushers: 636 feed rows
  over 211 steady states. Native Manual Copy Optimised completed with the same
  allocations. Both result sets passed physical equipment limits, grade-chart
  data checks and transport conservation (maximum error 6.9e-7 WMT).
- The completed separate soft-target, two-COS-zone validation was saved
  natively as `CC_Combined_OPF_Validation.prj` at 20:27. The original hard
  prepared project was preserved and verified by SHA256. Product volume and
  soft quality shortfalls are reported honestly; calculation completion does
  not indicate that the original production targets were met.
- Readiness now uses actual OPF product-arrival reports for build completion
  and quality, including scoped build fields for independent OPFs. Legacy
  nullable crusher/reclaimer quantity columns fall back to physical tonnes;
  explicit zeroes remain zero and malformed supplied values are rejected.
  Manual destination planning avoids propagating large DataFrame audit attrs.
- The native scheduled Support batch completed every enabled site and
  atomically published `C:\BlendMaster\SharedProjects\CC_Combined_OPF.prj`
  at 20:22:55. Read-back verified 19 chunks, 27 actual opening movements,
  inputs_ready outcomes and the original hard OPF01 phosphorus cap. The AMT
  six-column operational table was inspected natively with populated rows.
- Opening a shared file now binds its watcher to that actual source even
  when the publishing project inherited a template subscription. Planner
  working copies retain their upstream link; Support saves clear inherited
  subscriptions. A recovered Planner has detected the real new publication
  and is reviewing it with a fresh unsaved contingency edit of 14.
- All 1,256 tests passed in 52.880 seconds; normal `git diff --check` passed.
  Evidence in Playground: `bm_native_manual_validation.json`,
  `bm_native_completed_project_save.json`, `bm_shared_publication_verified.json`
  and `bm_subscription_full_tests.log`. Native chart rendering and final
  selective-import acceptance remain in progress.
