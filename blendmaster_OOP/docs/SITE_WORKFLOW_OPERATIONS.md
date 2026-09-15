# Site workflow and planner handoff

This desktop PoC uses the existing blending and reconciliation algorithms. The
workflow controller coordinates their inputs, permissions, completion and report
checks. It does not deploy a web application or create an AWS schedule.

## Roles and navigation

The trusted launcher sets `BLENDMASTER_ROLE` to `planner`, `support`, `owner` or
`agent`. The default is `planner`. Support, Owner and Agent can access all pages.
Planner can choose an existing site and change its planning start and periods;
mode, OPFs, operating crushers, field mappings and technical streams belong to
Support. Submitted operations also check the role. Project files cannot grant a
different role.

This environment variable is a desktop role boundary, not user authentication.
The future web service must obtain the role from an authenticated principal and
enforce these actions on the server.

| Area | Pages, in task order |
| --- | --- |
| Workspace | Site Configuration; Guidance Schedules; Stockpile Inventories; Grade Reconciliation; AMT Stockpiles; Product Targets; Destination Reconciliation; Decision Levers; Calendar; Optimised Blend Sequence; Manual Blending Dashboard; Manual Blend Sequence; Blend Plan; Material Destination Plan |
| Views | Database View; Expit Sequence; OPF Production Report; Grade Profiles (optimised/manual); Material Flow; Build and Depletion Profiles; Closing ROM Stocks Compliance |
| Support | Site Model Settings; Guidance Settings; Define Fields; Map Fields; Data Streams; Solver Configuration; Multi Feed Setup; Conveyors & COS; Database Reports; Site Automation; Decision Diagnostics; Legacy Agent Bridge |

Support > Site Model Settings includes **Reconcile AMT after chunking**. It is
unchecked by default: Grade Reconciliation matches each AMT component hex,
followed by AMT Stockpiles. When checked, AMT Stockpiles comes first and Grade
Reconciliation matches the combined lineage of each submitted chunk. Both roles
advance through this order on Submit; the automated preparation stages use the
same order. Inventory stockpiles remain individual reconciliation sources.

Independent OPF source-grade preparation waits until every included, positive
AMT footprint has submitted chunks and its required factors are approved. In
component mode, preparation combines the individually adjusted contributions
using the configured grade weights. In chunk mode, it applies the chunk's
approved factors to the aggregated modelled grades. Historical searches remain
manual; changing chunk membership in chunk mode requires approval for the new
chunk. Active chunks continue using their continuous-assay priors. Saved review
rows and Last adjusted dates remain available after submission.

The **Subset** dropdown sits beside **Nearest Crusher** in Stockpile Inventories.
Nearest Crusher supplies the initial assignment and auto-selection uses its
operating-crusher mapping plus a 2WP brand. Subset is authoritative for rehandle
routing: explicit movement rules take precedence, then Subset matches the ROM
area configured for a tipping point. Known crusher aliases are recognized.
Multiple-point planning requires a permitted route for every selected source;
an explicitly blank Subset requires correction. Reconciliation and independent
grade preparation cover only permitted source–OPF pairs. Single-point mode uses
the selected OPF.

The old Reports container is split across its destinations. Blend Plan keeps
optimised/manual plans and displays the active single-point, multiple-point or
combined-OPF mode. Quality, rounding, backup and direct-tip audit views remain.

Expit Sequence is available after site/start setup, an available 24HR import,
selected dig circuits and transaction reconciliation enabled in Guidance Settings.
Destination Reconciliation requires site/start, an available 2WP import and
inventories with Nearest Crusher assignments. Disabled pages explain missing
preparation in their tooltips. Grade Profiles and its Optimised/Manual pages
follow actual saved report rows in the active site's database; restored tab flags
cannot enable an empty report.

Embedded charts load automatically when an available page opens. Completed runs,
manual report changes, plan selection and AMT map preparation refresh the visible
chart; other charts update on their next visit. Local service startup is checked
in a background worker. There are no chart Load/Update buttons or blocking HTTP
refresh calls in the planner flow. Warehouse refresh and Submit actions retain
their existing meaning. Changing hidden tabs does not start a warehouse refresh.
The legacy bridge is retained under Support for compatibility; new scheduled
handoffs use the site workflow. Best-result selection is automatic.

Crusher Contribution remains relevant to single-point target scaling. It is
visible under Guidance Settings for that mode; multiple-point modes use their
physical-point Calendar configuration. The retired route-capacity editor remains
retired: Calendar owns the point's per-source Max Reclaim Rate.

## Configure a site

1. Launch as Support and configure/submit the site model, field definitions and
   mappings, technical data streams, guidance selections, solver and flow setup.
2. Save a site contract in Support > Site Automation. Export its JSON for an
   agent runner. Save Project to retain the contract across sessions.
3. Set delivery paths, timezone, required/optional sources, arrival windows,
   retention policy and refresh interval. Enable the desktop scheduler only for
   the intended site and handoff endpoint.
4. Run Prepare Inputs, then inspect the status and run receipt. Give the planner
   the prepared project once any reported input problems have been resolved.

Defaults are Australia/Perth, HI cycles/2WP/closing balance weekly Wednesday,
and 24HR daily 14:00–15:00. All are configurable. An arrival window is an
expectation, not an expiry of the previous accepted 24HR. Missing or invalid
replacement files can retain the last accepted version when configured; the
status reports the missed delivery. File modification time is the delivery
freshness proxy, so delivery systems should preserve meaningful timestamps.

Imports are validated and copied into a versioned local accepted-input folder
before replacement. Matching crushers, dig circuits and movement rules survive;
only identities absent from a valid new file are removed and recorded. Failed
imports leave the previous accepted version in place. No select-all fallback
silently chooses newly added equipment.

## Run and handoff

Prepare executes imports → inventory → guidance → reconciliation → AMT chunks →
expit → destination reconciliation → database preparation → validation. Plan
continues through optimisation and report preparation. Each run captures one
start time and site/database context; results from an obsolete context are
discarded. Cancel waits for an in-flight operation to stop before unlocking
inputs. Individual stages have a 30-minute timeout.

Submitting Stockpile Inventories prepares the new opening selection in a worker
while the previous saved plan remains available for viewing. Compatible AMT
footprints are reused; new or incompatible footprints are fetched. Refresh AMT
Data explicitly requests a fresh snapshot of the selected footprints. The inline
status offers cancellation, then Retry refresh or Keep previous inputs if the
request fails or is cancelled. Cancelling waits for an in-flight warehouse query
to return. Keeping the previous inputs also restores selection and edited
inventory cells.

Opening inventory and AMT tables are replaced together only after preparation
and validation succeed. Existing optimised and manual reports are retained.
Pending or failed refreshes block dependent calculations and project saving;
after success, the new opening version requires reconciliation preparation and
the old results remain historical until recalculated. Prepare Inputs also keeps
saved-result navigation available through its background stages. See
[inventory refresh implementation and verification](INVENTORY_REFRESH.md).

The desktop scheduler checks the **active site** while a Support, Owner or Agent
session is running. `refresh_minutes` controls the check interval; the source
cadences describe expected deliveries. It does not wake a closed application or
iterate inactive sites. Use a separate external job per site for unattended runs:

```powershell
Set-Location C:\BlendMaster\blendmaster_OOP
python -m execute.SiteWorkflow --project C:\Models\site.prj --contract C:\Models\site_contract.json --output C:\Handoffs\run_20260912 --endpoint prepare --start now
```

Use `--endpoint plan` only when optimisation is requested. `--start saved` reuses
the saved planning timestamp; a site-local ISO datetime is also accepted.
`--show` displays the desktop window. The runner requires this desktop Python/Qt
environment and the site's existing Snowflake credentials. The output directory
must be empty. It writes `run.json` and, on a completed handoff, `prepared.prj`.
The checkpoint is written atomically. Failure returns a nonzero exit code and
does not publish a successful handoff. The runner has a six-hour process deadline.

An AWS/web adapter will need a supported non-desktop execution host, job storage,
authentication, credential management and delivery locations. These are future
hosting integrations; the JSON contract and workflow stages define the boundary.

## Completion and release checks

Calendar retains submission/progress status. Calculation completion is separate
from physical inventory, Calendar equipment limits, product target attainment,
quality policy, destination readiness and input freshness. Lower manual equipment
rates are valid; rates above Calendar are blocked, including after rounding and
at export. Editing inputs after generation requires a new plan.

Quality/target/destination issues are shown for review with the existing business
policies unchanged. Physical, equipment or stale-input failures block export.
Saved plans from before input-version tracking carry an explicit unverified
freshness status and cannot be exported until regenerated. Export audits include
coverage/lineage and readiness evidence.

Manual recipes, rates, sequence and ratio-rounding policy have a separate input
revision. Editing those controls invalidates the manual plan without invalidating
an unchanged optimised plan. Converting an optimised plan retains fractional-second
durations so an exact Calendar-limited rate is not inflated by display rounding.
Freshness and saving use the current recipe inputs, including edits awaiting
submission. Display formatting retains the exact stored ratios. Saved manual
sequence controls and their Gantt are restored without another dashboard Submit.

For multiple tipping points and combined OPFs, **Manual Blending Dashboard →
Recipes and rates** provides a separate recipe set for every configured tipping
point. Add a Blend ID, enter source weights and optional reclaim rates, review
the opening/projected stock and grade estimates, and set manual crusher rates
by period. Zero weight excludes a source. Submit recipes, then use **Manual
Blend Sequence → Sequence editor** to add, remove or reorder blend rows and edit
their start and duration. **Join times in row order** makes the rows contiguous.
The chart shows the last calculated sequence; edits remain drafts until submitted.

**Steady States & Direct Tip** reviews the joint plan and accepts direct-tip
payloads by tipping point. The calculation uses a shared physical inventory with
each OPF's prepared chemistry, checks Calendar and recipe reclaim limits, and
uses the existing Conveyor/COS replay. A stockpile cannot reclaim to two points
at once, projected stock cannot be used before its receipt time, and a payload
cannot be used twice. Recipe and sequence drafts persist with their named manual
plan. Older saved manual allocations are converted into editable recipes when
first opened; clearing a tipping point does not reimport its old rows.

**Create manual plan from optimised…** belongs on the manual authoring pages.
Choose a saved optimised starting plan and a new manual plan name, then edit and
calculate that independent manual plan. **Blend Plan → Manual per tipping point**
contains **Edit manual recipes and sequence…**, point-specific backup selections,
**Export point PDF…**, and **Export all XLSX…**. Backup choices use that manual
plan's resolved destination evidence, and export validates the selected plan's
own saved inputs. A blocked or failed export opens an explanatory dialog.

Save Project and normal save-on-close write unique project files atomically in a
worker. The application remains open if a save fails. XLSX/PDF blend-plan exports
also generate their files in a worker and retain review warnings. Build and
Depletion Profiles shows 12 sources per page to keep large site views responsive.

Prepared Destination Reconciliation evidence is saved with its site, start,
file-version, inventory-area and lookback dependencies. Reopening an unchanged
handoff restores it without a warehouse query; changed dependencies require a
refresh. Existing projects without that saved evidence need one refresh and save.

Warehouse evidence and unchanged imports are reused. Project decoding, AMT map
decoding, field application and Auto reconciliation run in workers. Exact
reconciliation searches are reused only within a matching history/settings and
physical-source context. Edited baselines still have their grades recalculated;
changed tonnes or lineage cause a new search.
