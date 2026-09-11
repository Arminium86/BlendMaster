# Authoritative Data and Behavior Contracts

Task 1 of the BlendMaster major implementation plan. This document records the
contracts that later tasks build on. It reflects verified current behavior plus
the 72 clarification answers dated 4 September 2026.

Status: contracts only. No production logic is changed by this document.

Branch: `main_BlendMaster_ultimate_prod_streams`

## 1. Scope and precedence

Where a clarification answer and current code disagree, the answer defines the
target behavior and the gap is recorded as a Change below. Where they agree, the
code reference is authoritative and no change is required.

Two independent spatial hierarchies exist and must not be merged:

- Destination fallback (Q11), used for ROM destination resolution.
- Reconciliation factor fallback (Q32), used for grade adjustment.

They share the grade-block token vocabulary but differ in levels used.

## 2. 2WP (APS Mining.csv) column contract

Source of truth: `classes/ExpitDataHandler.py` `TRANSACTION_COLUMNS` and
`build_2wp_destination_guidance`.

| Purpose | Column |
| --- | --- |
| Source identity | `Source.FullName` |
| Source type | `Source.Type` |
| Source pit | `Source.Pit` (optional, defaulted when absent) |
| Expit material type | `MutexParcel.ORETYPE` |
| Destination identity | `Destination.Name`, falling back to `Destination.FullName` |
| Destination kind | `Destination.Type` |
| Window start | `Time.StartTime` |
| Window end | `Time.EndTime` |
| Tonnes | `Mining.wetTonnes` |
| Agent | `Agent.Name` |

Confirmed for the ROM build-order extractor (Q1):

- `Destination.Type` filters to stockpile destinations. The existing helpers
  already branch on a lowercased `Destination.Type` for `crusher`, so the
  stockpile branch follows the same normalisation.
- The extractor answers, per expit material type, which ROM destinations are
  planned and in what order.
- Material type is derived from the grade-block full name leading letters
  (`classes/GradeBlockIdentity.py` `grade_block_material_type`), exactly the
  `BA72_63 -> BA` rule with no exception table (Q4).
- Tonnes are ROM WMT.
- `DESTINATION_GUIDANCE_VERSION` must increment when this contract changes.

## 3. Snowflake movement and activity contract

Table: `AA_OPERATIONS_MANAGEMENT.SELFSERVICE.INVENTORY_EXPIT_REHANDLE_TRANSACTIONS`
Reference: `setup/sql/opf_daily_reconciliation.sql`.

Authoritative filters, reused unchanged for recent-destination activity (Q5):

- `IS_DELETED = FALSE`
- `MOVEMENT_CLASSIFICATION = 'Rehandle Ore'`
- `MOVEMENT_SUBCLASSIFICATION = 'Rehandle Ore Primary'`

Columns: `DESTINATION`, `SOURCE`, `SHIFT_DATE`, `WMT_REPORTING`, `DMT`, `WMT`,
`MOVEMENT_PRODUCT`, `OPERATION`, plus `FE/SIO2/AL2O3/P/MN` and their
`PROD1_*` / `PROD2_*` variants.

`DESTINATION` for stockpile destinations carries the full stockpile name with a
trailing `_<build number>` part. That suffix is the inventory-stockpile to
grade-block lineage key (Q43).

Activity window (Q5, Q6): qualifying inbound ExPit movement to the destination,
default 12 hours, user-configurable, measured immediately before scenario start.

Destination capacity (Q9, Q10): user-entered remaining capacity is ROM WMT and
is consumed only by final non-direct-tipped tonnes.
At a capacity boundary, keep the remaining non-direct-tipped payload whole at
the current destination, record the overrun, and advance the next payload
(confirmed 2026-09-09 during Task 23).

## 4. Grade-block identity and token mapping

Canonical name: `Reserves/mine/pit/stage/bench/blast/flitch/material_slice`.

`Reserves` and `mine` are constant for a model and are intentionally dropped.
The remaining six tokens are the operational identity and are already parsed.

| Business level | Parser token | Snowflake column |
| --- | --- | --- |
| pit | `location` | `LOCATION_NO` |
| stage | `phase` | `PHASE` |
| bench | `blast_rl` | `BLAST_RL` |
| blast | `blast_no` | `BLAST_NO` |
| flitch | `flitch_rl` | `FLITCH_RL` |
| material_slice | `block` | `GB_NAME` |

Verified against both ends of the pipeline:

- Parser order in `classes/ExpitSequenceReconciler.py` `polygon_lookup_name`.
- `FULL_NAME` construction in `classes/ExpitDataHandler.py`, which concatenates
  `LOCATION_NO, PHASE, BLAST_RL, BLAST_NO, FLITCH_RL, GB_NAME` in that order
  with zero padding of 2, 4 and 4 on `PHASE`, `BLAST_RL` and `FLITCH_RL`.

Consequence: no parser change is required for Q11 or Q32. Both hierarchies are
expressible as prefixes of the existing six-token key.

Normalisation rules that must be preserved:

- `grade_block_key` drops a leading `RESERVE`/`RESERVES` token, keeps the final
  six tokens, and joins with `|`.
- Numeric tokens are normalised via `_normal_number_token`, so `01` and `1`
  converge.
- `parent_grade_block_name` strips the APS slice suffix `_<digits>`. Advanced
  reconciliation keys on the parent, not the slice.
- Waste routes are excluded by `is_route_only_waste`.

### 4.1 Destination fallback ladder (Q11)

Applies to ROM destination resolution. History window is the whole imported 2WP
guidance schedule.

1. Same material type in same pit + stage + bench + flitch
2. Same material type in same pit + stage + bench
3. Same material type in same pit + stage
4. Same material type in same pit
5. Any material type except waste in same pit + stage + bench + flitch

Ties at fallback 2 resolve by shortest haul cycle (Q12). Pit level derives from
grade-block name parts and is already used by existing fallback logic.

### 4.2 Reconciliation fallback ladder (Q32)

Applies to grade adjustment. Grain is
OPF + brand + analyte + spatial level. Note this ladder includes `blast`, which
the destination ladder omits.

1. pit + stage + bench + blast + flitch + material type
2. pit + stage + bench + blast + material type
3. pit + stage + bench + material type
4. pit + stage + material type
5. pit + material type
6. Global OPF/brand/analyte factor (existing behavior)

Resolution is per spatial level, not per analyte: once the best level is found,
all analytes are processed at that level, and blend and regression factors use
the same level (Q40).

## 5. Reconciliation factor attribution and lookback

Current behavior: `setup/DataStreamReconciliation.py` holds
`LOOKBACK_DAYS = (7, 14, 21, 28, 30)` and
`CB_CAMPAIGN_LOOKBACK_DAYS = (*LOOKBACK_DAYS, 60)`, selecting the shortest
window that yields a value, tonne-weighted by `WMT_REPORTING`. Factors are
resolved per OPF, brand and analyte.

### 5.1 Lookback modes (Q31, Q33)

Three selectable modes, configured in the reconciliation tab:

1. Trailing calendar days before scenario start. This is current behavior and
   remains available as Standard mode.
2. Last N production days containing the brand. Requires an N input.
3. Final N days of the most recent production campaign. Requires an N input.

### 5.2 Factor attribution (Q35, Q38)

- Factors are derived as today: back-calculated actual feed grade from assays
  divided by modelled feed grade over a period.
- A period factor becomes eligible for every grade block that contributed
  during that period.
- Aggregating factors across periods weights by total period feed.
- Factors are computed at the finest grain the assay table supports, which is
  shift level.
- Application is restricted to sources whose grade blocks are spatially and
  compositionally close to the blocks the factor came from.
- Only sources with lineage are adjusted. Without lineage, the global factor
  applies.
- Identical logic applies to both blend and regression reconciliation.

### 5.3 Factor sourcing methods (Q38, Q42)

Three advanced methods are available following the user's 6 September 2026
request to bring automatic evidence-match maximisation forward before Task 9
(Task 8A), and the subsequent source-selection clarification:

1. Lookback time window.
2. Spatial and compositional relevance, bounded by a max lookback window.
3. Auto-maximise evidence match score across spatial reconciliation and the three
   supported lookback families, independently comparing every eligible spatial
   fallback level within each candidate window, comparing component-based and
   shared whole-source histories (clarified after Task 11, 6–7 September).

Standard global reconciliation remains the default. The earlier deferral of
automatic maximisation is superseded by Task 8A.

Spatial is source-directed. Within the maximum lookback, eligible historical
shifts are ranked by their whole-feed composition/spatial-address match to the
whole source being adjusted. At the first spatial level supporting all ten
factor series, retain the highest-matching shifts until every series satisfies
its cell/analyte minimum distinct production dates. One shared score cutoff
applies across the ten series in that component; all ties at the cutoff are
included. Selected dates can be non-consecutive. The date/score cutoff can differ
between components according to their evidence and local guardrails, while the
match reference is always the complete inventory/hex composition.

Lookback retains all eligible spatially matching shifts in the chosen time
window. It does not rank or trim them by source match. This makes it distinct
from Spatial. A calendar/production/campaign subset can have a higher aggregate
match score because its mix of shift-feed tonnes differs; Auto compares both.

Auto selects one history approach and method/window policy per physical inventory stockpile or AMT
hex and brand, scored against that source's complete composition. The search
covers whole-day spatial horizons, trailing completed calendar dates, last N
brand production dates, and final N dates of the latest consecutive production
campaign. Spatial candidates apply the source-match ranking rule; lookback
candidates keep all eligible periods in their window. This is a deterministic
ranking-prefix search, not unrestricted optimisation over all shift subsets. Minimum
production days and maximum calendar lookback are user guardrails, including
local cell/analyte settings. Saved manual window choices are retained for the
other methods; Auto chooses the method and N within those guardrails.

Auto's retained component-based approach compares all five spatial levels for each component in every candidate
window, even when a finer level already has enough history. The best-scoring
eligible level is selected per component; one level must still support all ten
factor series. Ordinary Spatial and Lookback retain the first-sufficient-level
rule. Manual factors apply after selection without increasing the evidence
match score. The full maximum-lookback ordinary Spatial result remains the
comparison baseline; Auto also evaluates every level within that full horizon.

The additional shared-history approach tests all five common levels in each
window. Each selected shift must contain every known source spatial/material
group at that level (positive presence, without a percentage threshold), support
all ten valid positive factors, and obey every local window bound. The strictest
minimum dates and tightest maximum apply. One shared set supplies all known
components and ten series; individual whole-shift scores are feed-weighted,
never calculated from pooled shift compositions. This candidate competes with
the component-based result using the same score. No shared candidate is forced
when unavailable or worse, and no additional input is required. Manual factors
still apply afterward to their original cell/analyte; unknown source mass retains
zero evidence and global factors. Lookback and Spatial modes are unchanged.

Ties prefer less global evidence, finer fallback levels, more
supporting production dates, then more period feed, then component-based history, with deterministic policy
order resolving remaining ties. Score differences below numerical precision
(scores rounded to ten decimal places for ranking) are treated as ties.

Fixed physical source WMT makes these independent choices maximise the existing
WMT-weighted overall score within the supported search space. Missing lineage
remains in the denominator with zero evidence match score. AMT chunks aggregate hex
choices when available; before chunking, footprints show a preview. Selected
windows, history approaches, baseline evidence match score and gain in percentage points are retained in
source/chunk audits, the review and CSV/Database View fields. Component evidence
also records the five-level comparison, eligibility and scores for the selected
method/window. See [the source-matching clarification](RECONCILIATION_EVIDENCE_MATCH.md)
and [Auto's current shared-history mental map](RECONCILIATION_SHARED_HISTORY.md).

### 5.3.1 Per-hex lineage weighting (Q39)

Factors resolve per lineage component and are then tonne-weighted to the hex.
Where a hex is 40 percent GB1 and 60 percent GB2, the applied factor is the
40/60 weighted combination of the factors resolved for GB1 and GB2. A single
dominant factor for the whole hex is not used.

Composition-ratio mismatch lowers the evidence match score. Under the user's
6 September source-selection clarification it also influences which shifts
Spatial selects. The score never multiplies a factor: selected period factors
retain total-period-feed WMT weighting, followed by physical source fractions.

### 5.3.2 Window and fallback exhaustion (Q34, Q37, Q41)

- Grade-block tokens are as mapped in section 4. No additional token vocabulary
  is introduced.
- Spatial and Lookback walk the section 4.2 ladder until a level qualifies within
  their selected window. Auto independently compares all five spatial levels in
  each candidate window; insufficient evidence never relaxes that window's bounds.
- When every spatial level is exhausted, the terminal fallback is the standard
  global OPF/brand/analyte factor, never 1.0 and never a blocked submission.
- Minimum production days and maximum lookback window bound the search (Q42).

### 5.4 Evidence match score (Q32, Q42, Q45; terminology clarified 6 September)

- Evidence match score is 100 percent when every grade block in the source period has the
  highest spatial and compositional relevance to the source being adjusted, and
  the composition ratios match.
- The score decreases as ratios deviate, spatial/material overlap weakens, and
  less relevant material enters the same feed period. All candidates are scored
  over the same six spatial/compositional resolutions. Choosing a broader
  fallback level carries no extra deduction; specificity is a tie-break only.
- Inventory stockpiles are adjusted as a single weighted average with no
  chunking, and evidence match score is reported at that level.
- AMT stockpiles are adjusted per hexagon, each hex carrying its own
  evidence match score, reported in Database View at AMT chunk level, where a chunk may
  span several hexagons.
- An overall evidence match score across all adjusted sources is also reported.
- Required provenance in reports: selected fallback level and
  evidence match score. Uncertainty is removed from the Data Streams review,
  Database View and CSV output. The score is diagnostic similarity, not a
  calibrated grade-accuracy probability. Legacy internal `confidence_percent`
  and `uncertainty_percent` fields remain readable for saved-state compatibility.
  The separate EXPIT geometry/replay reliability classification in
  `classes/ExpitSequenceReconciler.py` is not this factor-evidence metric.

### 5.5 Lineage sources (Q36, Q43)

- AMT hexes: existing AMT lineage queries already expose grade-block full names
  per hexagon.
- Inventory stockpiles: lineage derives from opening stockpile inventory plus
  `INVENTORY_EXPIT_REHANDLE_TRANSACTIONS.DESTINATION`, using the trailing build
  number part.
- APS grade blocks are not adjusted in BlendMaster; they arrive pre-adjusted
  and mapped.

### 5.6 Overrides (Q44)

Editable at OPF + brand + analyte + pit + stage + bench + blast + flitch +
material type. This is a large matrix and needs a navigable UI.

## 6. Grade streams and analytes

`classes/GradeStreams.py` is authoritative.

- Analytes: `("fe", "si", "al", "p", "mn")`, matching Snowflake
  `FE, SIO2, AL2O3, P, MN`.
- Streams: `insitu`, `modelled_rom`, `adjusted_rom`, `modelled_product`,
  `adjusted_product`, defaulting to `adjusted_product`.
- Fallback is independent per analyte with a retained warning.
- Tonne bases stay separate from grade selection: crusher, reclaimer and
  product-build quantity fields are configured independently, and physical
  depletion and balances remain ROM WMT.

Any grade comparison must compare like with like. Source-similarity scoring
uses the selected grade stream so product grades compare against product
targets, never ROM against product (Q63, Q65).

## 7. Product assay timestamp and aggregation grain

For the OPF Production Report:

- Aggregation grain is user-selectable (Q67).
- Default window is the last 24 hours relative to BlendMaster scenario start,
  not wall clock (Q68).
- Display timezone is AWST (Q68).
- Analytes are the existing five only (Q69).
- Combined-OPF mode shows per-OPF series and a combined series (Q70).
- Charts show LQL/HQL lines plus the active 2WP/Product Target grade, and mark
  changes when the target build changes (Q71).
- Refresh is on demand plus a configurable interval. When Snowflake is
  unavailable, show an explicit message and render from cached data (Q72).

Tasks 14–15 implementation (7 September 2026): the report uses production
`TRANSACTION_DATETIME` in AWST from the reconciliation product table,
`AA_OPERATIONS_MANAGEMENT.SELFSERVICE.INVENTORY_OPF_PRODUCT`. Assay sample time
and warehouse update time are separate provenance; missing sample times never
move or remove a production observation. This timestamp choice follows inspection
of the warehouse schema and records; Q68 explicitly confirms AWST and scenario-
relative defaults, but does not name a source timestamp column.

Raw observations retain production records, shift aggregates use source shift
assignments (06:00/18:00), and daily aggregates use AWST calendar days. Both
weighted grains use valid-assay positive DMT independently per analyte. Queries
include the start and exclude the end; edge periods can be partial. Combined
values are calculated from constituent records and identify partial OPF coverage.
Cache fallback matches the exact source/query/window/OPFs and retains its original
fetch timestamp. Refresh intervals run while the report is visible and keep the
selected window fixed.

Target/LQL/HQL lines use canonical Product Targets specifications, with dated
planning intervals clipped to the window and build changes marked. Undated
targets are explicitly current references; future builds are not assumed to
describe historical production. Conflicting dated ownership is reported and
omitted. No central target is inferred from hard Min/Max bounds; no combined
quality specification is invented. The report is descriptive and introduces no
optimisation penalty. Task 16 adds target-mode configuration in Product Targets.

See [OPF Production Report](OPF_PRODUCTION_REPORT.md) for service bounds,
the UI review guide and instructions for generating local screenshots.

## 8. Product Targets, LQL/HQL and target modes

Current behavior: `setup/PlanningPlanTargets.py` builds product-build rows with
`build_id`, `brand`, `byproduct`, `cbfl_campaign`, `target_tonnes`,
`planning_target_tonnes`, `crusher_contribution_ratio`, `opf`, `crusher` and
planning period fields. Grades arrive as `fe`, `si`, `al`, `p`, `mn`. 2WP gives
an Fe lower bound and contaminant upper bounds, leaving opposite bounds open.
Task 12 additionally seeds `target_<analyte>_target` from the planned grade and
provides optional row-owned LQL/HQL fields. Task 16 adds an explicit Hard/Soft
mode per build. Hard retains existing Min/Max enforcement. Tasks 17–19 now
implement Soft optimisation, source similarity and quality-result reporting.

Contracts:

- Task 11 is implemented: the tab is **Product Targets**. The canonical page,
  project/scenario/calendar key and agent workflow section are `product_targets`.
  Older `product_build_settings` projects and agent payloads remain readable;
  explicit current values (including an empty list) take precedence over aliases.
  Migration includes active/inactive scenarios and calendar inputs. Saved page
  names and legacy flat-tab indices still resolve; explicit current page state
  wins. Omitted agent sections leave rows unchanged, while an explicit empty
  list clears them. Internal solver/reporting `product_build_settings` APIs
  retain their established parameters; new saves use the current key.
- LQL and HQL are properties of a brand, and therefore of a product-build row,
  per analyte. They are row entries like the existing Min and Max, not a
  separate table (Q52, Q55).
- Hard mode retains exactly the current Min/Max behavior including open
  opposite bounds (Q56).
- Soft mode adds a Target between LQL and HQL. Target defaults to the 2WP
  planned grade, is manually editable, and is tonne-weighted when consecutive
  2WP builds are grouped (Q58).
- LQL/HQL are optionally hard bounds or soft bounds with a larger penalty than
  Target deviation (Q57).
- Lump and fines may carry separate LQL/HQL even on the same brand. This is
  safe because `classes/CloudbreakProductSplit.py` back-calculates lump grades
  so lump and fines recombine to the adjusted head grade (Q53).
- LQL/HQL are manually entered for now (Q54).
- Quality-direction caption clarification (10 September 2026): Fe uses
  **LQL ≤ Target ≤ HQL**; contaminants Si, Al, P and Mn use
  **HQL ≤ Target ≤ LQL**. LQL/HQL mean lower/higher *quality*, not necessarily
  lower/higher numerical grade. Equality at a limit retains existing acceptance.
  Product Targets, chart legends/details, manual grade profiles, solver messages
  and Product Quality Results/CSV use these analyte-specific labels. Breach
  columns are **LQL breach** and **HQL breach**, with the direction determined
  by the analyte.
  For saved-project and raw database compatibility, schema-v1 quality records,
  schema-v2 target rows and raw audit `_lql`/`_hql` fields retain their original
  numerical lower/upper meanings. Contaminant HQL therefore maps to the legacy
  `_lql` key and contaminant LQL to `_hql`; raw `below_lql`/`above_hql` remain
  numerical lower/upper breaches. The presentation mapping also applies to old
  saved audits. No stored value, numerical constraint or penalty changes, and
  agent payload instructions explicitly document this compatibility mapping.
  Validation: 113 focused checks passed, including real CBC boundary/penalty
  decisions, saved-target round trips, saved-audit CSV, chart line styles and
  manual profile captions. Product Targets and Product Quality Results were
  rendered and visually checked using illustrative test data.
- Task 12 is implemented: **Grade fields** selects Min/Max, LQL/Target/HQL or
  all columns on the existing Product Targets rows. Blank specifications remain
  `None`; finite supplied percentages must satisfy numerical lower bound <=
  Target <= numerical upper bound wherever those values are present, using the
  quality-direction captions above. Zero is preserved. OPF/lane ownership and planning
  provenance follow the row through edits, deletion, persistence and agents.
- Targets group by tonnes only within compatible consecutive OPF/brand/lane
  rows; different manual quality limits prevent merging. Legacy hard bounds
  do not imply a central Target. The validated versioned reference configuration
  reaches solver inputs; quality fields also reach product/progress reports.
  In Hard mode, on-spec checks still use Min/Max. Soft checks use the entered
  LQL/HQL; a permitted Soft breach is reported as a breach and carries its penalty.
- Three displayed decimals apply only to 2WP-imported product targets;
  calculations keep full precision and everything else is unchanged (Q51).
- Task 13 is implemented: imported-row Min/Max and central Target cells format
  only their painted text to three decimals. Editing, copying, validation,
  project/agent round trips, solver inputs and numeric reports retain the full
  value. Manual-row and LQL/HQL formats remain unchanged. Previously rounded
  saved values require a fresh 2WP import to recover the planned precision.
- Task 16 is implemented: each build has a **Target mode** (Hard or Soft).
  Selecting Soft exposes its LQL/Target/HQL columns and enables the selected
  build's **Evaluate targets against** control and five analyte **LQL/HQL**
  controls. Evaluation choices are **Steady-state product output** and
  **Cumulative active build average**. Each analyte independently selects
  **Hard limits** or **Soft limits (higher penalty)**. These choices belong to
  the row, including its OPF and lump/fines lane; they are not global settings.
- `product_target_schema_version=2` persists `target_mode`,
  `target_evaluation_basis` and `target_<analyte>_limit_mode`. Absent/v1 settings
  migrate to Hard, steady-state evaluation and hard LQL/HQL defaults. Existing
  Min/Max values and open bounds remain unchanged. Unknown versions or modes
  are rejected rather than silently converted to Hard. Active/inactive
  scenarios, calendar inputs and agent payloads preserve these settings.
- Changing mode preserves both sets of grade values and the saved Soft settings.
  **Grade fields** remains a display choice; **All grade fields** can show both
  sets. Compatible consecutive builds retain tonne-weighted Targets; different
  modes, evaluation bases or analyte limit modes prevent grouping.
- Task 17 replaces the temporary Task 16 execution restriction. Soft builds can
  run; the inline note points to **Setup > Solver Configuration > Soft Product Grades**.
  Target deviation and optional Soft LQL/HQL breaches enter the objective.
  Hard LQL/HQL remain constraints even with zero/disabled penalty weights.
  Soft ignores the retained legacy Min/Max values. Calendar bounds and other
  operational constraints remain independent. Legacy Hard repair/completion
  switches do not change the selected Soft evaluation basis or turn permitted
  Soft breaches into failed Hard solves.

Task 16 UI review: open **Product Targets**, confirm an existing build opens in
**Hard**, then change one row to **Soft**. Enter/review its LQL/Target/HQL, select
an evaluation basis and change an analyte's LQL/HQL behavior. Select another row
to check independence, switch back to Hard and Soft to check retained values,
then Save/Load Project to check persistence. Soft execution is now available;
the Tasks 17–19 review steps appear below.

Validation (2026-09-09): all 823 automated checks passed, including migration,
UI interaction, row ownership,
grouping, precision and execution-boundary checks; native Windows application
`.prj` save/restore (warehouse reload stubbed). Illustrative native screenshots
are local under `docs/screenshots/task16/` and ignored by Git.

### 8.1 Penalty formulation

Task 17 is implemented. The following choices are saved per scenario under
`solver_config.soft_grade_preferences` (schema v1), including project and agent
round trips. Each build retains its independent mode, evaluation basis and
analyte limit modes.

The solver is PuLP with CBC (`classes/Optimizer.py`), a mixed integer linear
programme. CBC cannot express a true quadratic objective.

Contract: penalties are piecewise-linear. Target deviation uses linear absolute
deviation scaled by tonnes, with additional breakpoints so marginal penalty
increases as deviation grows, approximating quadratic behavior without changing
solver technology (Q59).

Reference shape from operations (Q60):

    net grade penalty = |per-analyte penalty input * (actual - target) * 100 * tonnes|

Per-analyte inputs normalise the differing scales of Fe, Si/Al and P/Mn. A
larger multiplier applies beyond LQL/HQL when those are configured as soft.

The editable starting defaults are Target weight 1, analyte penalty weights 1,
LQL/HQL breach multiplier 5, and scales of 1 percentage point for Fe/Si/Al and
0.01 percentage points for P/Mn. Penalty-enabled checkboxes control both Target
and Soft-limit penalties for an analyte. A zero Target weight disables those
penalties; it does not disable hard bounds. Existing throughput/cost and other
incentives keep their existing weights, so these preferences trade against them
in the same objective. These are objective units, not a financial forecast.

For normalized distance `d = abs(actual - Target) / scale`, the default increasing
shape is `f(d) = d + 2*max(d-1, 0) + 2*max(d-2, 0)`, with marginal slopes 1, 3 and
5. **Linear absolute deviation** instead uses `f(d) = d`. Target penalty is
`100 * Target weight * analyte weight * W * f(d)`. Soft-limit breaches add
`100 * Target weight * analyte weight * W * breach multiplier *
(LQL breach + HQL breach) / scale`. For Fe these are below LQL and above HQL;
for contaminants they are above LQL and below HQL. Missing specifications
contribute no penalty.

`W` is the analyte's declared grade-weight tonnes (typically product DMT), the
same denominator used to calculate its actual grade. It is not silently replaced
by physical ROM WMT or a different product quantity. The LP uses grade-metal
deviation and scaled tonne breakpoints, avoiding division by variable tonnes.
The one-product transaction/report path now preserves these declared weights
through the runtime accumulator as well as lump/fines paths.

Evaluation basis is user-selectable between per-steady-state product output and
cumulative active build average, and applies whether or not the build completes
within the horizon (Q61, Q62).

**Steady-state product output** evaluates only the incoming addition.
**Cumulative active build average** evaluates the opening build plus that addition,
and its objective contribution is closing penalty minus the fixed opening penalty.
This avoids charging prior material twice and allows negative applied penalties
when a decision corrects an earlier deviation. Every partial cumulative build
uses the selected rules; enforcement does not wait for build completion.

### 8.2 Source similarity

- Optionally reward closeness to target, penalise inter-source variance, or
  both. Variance is measured against the target (Q63).
- Applies to inventory and AMT stockpile sources, optionally to direct-tip
  grade blocks (Q64).
- Per-analyte enable plus configurable weights, using the selected grade
  stream (Q65).
- User-configurable weights rank soft-target and similarity against throughput,
  cash/cost, direct-tip, brand guidance, balance and existing source
  preference levers (Q66).

Task 18 is implemented under **Soft Product Grades**. **Source similarity** has
Off (default), Closeness reward, Dispersion penalty and Both choices, separate
global closeness/dispersion weights (default 1), and per-analyte enable/weight
controls (default enabled/1). Similarity is independent of the Target-penalty
checkboxes. **Include direct-tip grade blocks** is off by default; inventory and
AMT stockpiles are included whenever similarity is active.

Each source uses `d = abs(source product grade - Target) / scale`. Closeness earns
`100 * closeness weight * analyte similarity weight * W / (1+d)`; dispersion adds
`100 * dispersion weight * analyte similarity weight * W * d^2`. These are linear
coefficients of selected source tonnes, since each source grade is fixed for a
solve. Dispersion is squared distance to Target, not variance around the selected
blend's mean. Thus a 50/50 mixture of Fe 56 and 60 at Target 58 has zero blended
Target deviation but dispersion score 4 at scale 1; a source at Fe 58 scores 0.

The active build's brand and selected product grade stream drive the one-product
comparison. Lump/fines use the same explicitly mapped lane product grades and
weights as their quality constraints. Similarity rejects an insitu/ROM stream
instead of comparing it with a product Target. A missing Target disables that
analyte's similarity. Only newly selected source material enters each decision's
similarity objective; cumulative report scores also describe material already
in the build. Source metrics are calculated before parent grade-block grouping,
so averaging displayed sources cannot erase their dispersion.

### 8.3 Quality results and UI review

Task 19 is implemented. **Product Targets > Quality Results** reads the current
optimised or manual plan's saved quality audit. Choose either evaluation grain,
filter an analyte/build/OPF/brand, and switch **Grades and limits**, **Penalties and
similarity** or **All fields**. The viewer shows Actual grade, Target, LQL/HQL,
signed Target deviation, both breach magnitudes, limit mode, status, grade-weight
tonnes, penalty components and source scores. All fields also includes the active
lower/upper bounds, hard-limit status, evaluation basis and timestamp. **Export
CSV** exports the filtered rows at full precision, including hidden fields.

Audit rows are unique per build/lane/steady state/analyte. Repeated source rows do
not multiply totals. Applied penalties appear only at the build's selected
evaluation basis; a blank in the other view means it was not the applied basis.
Source closeness is the weighted mean of `1/(1+d)` (higher is closer); dispersion
is the weighted mean of `d^2` (lower is closer). Applied similarity penalty is
dispersion penalty minus closeness reward for the new addition. Target mode and
quality status accompany saved blend/product-build reports; diagnostics and
product-grade chart hovers identify Soft targets and limit modes explicitly.
Legacy reports without this audit display a no-data explanation and need a fresh
run/evaluation to populate it. The OPF Production Report remains observational.

Review Tasks 17–19 by setting one build to Soft, choosing its evaluation basis
and limit modes, then configuring **Soft Product Grades** in **Setup > Solver Configuration**.
Run a plan (or evaluate a manual plan), open **Quality Results**, and compare the
two evaluation views and the two column views. Check a Soft breach and its
penalty, then compare Source similarity Off and Both. Save/reload the project
to check settings, and export a filtered CSV to inspect full precision. Existing
Calendar hard bounds may limit which blends are feasible independently of Soft
product preferences.

Validation on 2026-09-09: all 855 automated checks passed, including actual CBC
decisions, declared-weight and
cumulative accounting, manual/optimised report agreement, source grouping,
SQLite/CSV round trips, native filter/empty states, and a full Windows application
project save/load with warehouse reload stubbed. Illustrative screenshots are
local and Git-ignored under `docs/screenshots/task19/`.

## 9. Destination sequencing and ownership

- The 2WP planned destination order is authoritative for BlendMaster, but
  actual timing may lead or lag the plan. The planned order is reconciled
  against actual movements so material is assigned to the correct destination,
  and material type joins the key (Q7).
- The in-progress destination is the one with the most recent qualifying
  inbound movement in the activity window (Q7).
- When the first destination has never been active, assign the latest actual
  destination for the most spatially relevant grade block from
  `INVENTORY_EXPIT_REHANDLE_TRANSACTIONS` (Q8).
- A stockpile appearing twice with other destinations between remains one
  destination, but build instances increment on turnover. A stockpile is one
  build instance while it has not turned over; going build to reclaim and back
  to build starts a new instance (Q3).
- ROM area uses the existing Nearest Crusher value from Stockpile Inventories,
  not haul-distance grouping (Q2). Source:
  `classes/HaulCycleDataHandler.py` `build_nearest_crusher_routes`.

### 9.1 2WP ROM build order (Task 20)

Implemented in `classes/DestinationBuildOrder.py`. The extractor reads the same
Mining.csv source, destination, time and wet-tonne columns as the existing 2WP
guidance. Only positive Reserve-to-Stockpile movements enter the order. ROM area
uses the stockpile's displayed Nearest Crusher; material type uses the existing
leading-letter parser. Missing/conflicting ROM mappings, invalid records and
non-ROM movements remain visible in the row audit.

Orders are per ROM area and material type, ordered by first inbound time with
CSV record order breaking equal-time ties explicitly. Interleaved returns to a
stockpile remain in the same build instance. A completed reclaim interval between
inbound build periods starts a new instance. Reclaim evidence uses the existing
Flow/PlantAgent/OriginalSource-to-Crusher convention and explicit Stockpile-to-
Crusher records. Overlapping build/reclaim periods raise warnings; they do not
invent a turnover. Physical build-instance identity is shared across materials.

The input signature covers the file contents, ROM mapping and extractor version.
The active scenario database receives `destination_build_order` and
`destination_build_order_audit`, including source signatures, CSV record links,
planned ROM WMT, build instances, sequence positions and exclusion reasons.

### 9.2 Recent destination activity (Task 21)

Implemented in `setup/RecentDestinationActivity.py` and
`setup/sql/recent_destination_activity.sql`. The query window is half-open:
`[scenario start - lookback, scenario start)`, in AWST. Default lookback is
12 hours; the UI allows 0.01–744 hours and displays the exact AWST boundaries.
Scenario site codes map to warehouse `OPERATION` names: CC → Christmas Creek,
CB → Cloudbreak, KV/VK → Kings, FT → Firetail, EW → Eliwana, IB → Iron Bridge.
It filters that operation,
mapped stockpile footprints (`DESTINATION_FMS`), undeleted PrimaryMovement rows,
and ExPit / Expit Ore / Expit Ore classifications. Waste and Expit Ore Direct
Feed do not establish ROM activity. The original bounded warehouse check verified
classifications, but did not exercise a scenario site code. A live-session check
on 2026-09-09 found and corrected the code/name mismatch. The CC scenario window
17 August 19:51:49–18 August 19:51:49 AWST returned 621 qualifying movements after
stockpile mapping (622 before mapping). The operation name is part of the exact
cache request, so previous empty code-only results cannot be reused.

Actual grade-block identity determines material type. The latest qualifying
inbound timestamp establishes the detected physical destination, regardless of
tonnes. Several active destinations produce a warning; equal latest timestamps
remain ambiguous. A detected destination outside the planned order is explicit.
When the same stockpile has several planned build instances, dates alone cannot
resolve which instance is active because actual progress may lead or lag 2WP.

Cache keys include site, scenario time, lookback, full source-data signature,
ROM mapping and query version/content. Cached payloads carry a record signature
and fetched timestamp. Fresh cache reuse lasts five minutes; Refresh re-queries.
Offline fallback uses only an exact matching request and is labelled explicitly.
No matching cache is an unavailable state, not a zero-movement result. Queries
have a 60-second statement timeout and a 200,000-record bound; caches retain at
most 24 snapshots.

### 9.3 Destination Reconciliation setup (Task 22)

**Setup > Destination Reconciliation**, immediately after Stockpile Inventories, shows:

- **Progress:** detected destination, current build instance, previous/next
  instances, selection basis and remaining assignable ROM WMT per ROM/material.
  Selecting a row reveals its full extracted order and warnings.
- **2WP Build order:** dated build instances, planned tonnes and source CSV records.
- **Actual movements:** inbound timestamps, tonnes, source grade blocks,
  actual destination builds and movement IDs.
- **2WP row audit:** defaults to included ROM inbound records and order linkage.
  The Show filter exposes Excluded ROM inbound, Reclaim evidence or All rows,
  with displayed/total CSV record counts. Reclaim rows preserve the CSV destination
  and identify the Reclaimed stockpile separately; they remain necessary for
  build → reclaim → build detection. Unrelated rows remain available in All rows
  and the database audit. Filtering does not change extraction or build instances.

The Current build instance control permits an explicit reviewed selection and
labels it User selected. It overrides automatic detection of the current position
within the existing 2WP order; it cannot add, replace or reorder planned
destinations. The tab therefore remains named Progress. Automatic detection
remains separately visible. Valid edits apply immediately to the current scenario;
project Save persists them, so no separate Submit action is required. Blank
remaining tonnes means not set; zero explicitly means no remaining capacity.
Remaining tonnes belong to the physical build instance, so the same instance
shown under different materials shares one value. Values retain full precision.

Settings use `destination_progress_settings` schema v1, persist per site scenario
and through `.prj` save/load, and default to 12 hours with no entered capacity in
legacy projects. Scenario-time or source-signature changes clear previous
selections/capacities with an inline notice. Lookback changes clear manual
instance selection. Late asynchronous results cannot overwrite another context.
Large read-only evidence tables render cells on demand; a 50,000-row audit was
checked without dropping records. Extraction reads only the required CSV columns,
while its source signature still covers the complete file. Reviewed-instance
changes do not rebuild the unchanged evidence tables.

Validation on 2026-09-09: 879 automated checks passed. Native Windows checks cover
loading, fresh/cached/offline data, no data, repeated-instance ambiguity, reviewed
selection, capacity edits, compact layout and actual application project
save/load (the unrelated warehouse inventory reload was stubbed). Illustrative
screenshots are local under `docs/screenshots/task22/` and ignored by Git. The
current-session review also verified 621 actual movements, all 51 unchanged build
order entries, the corrected reclaim route and audit filters across 242,824 CSV
records (26,868 included ROM inbound).

Review: import 2WP Mining.csv, load Stockpile Inventories with Nearest Crusher,
then open Destination Reconciliation. Compare the extracted order and activity, review
any ambiguous current instance, enter remaining ROM WMT, and save/reopen the
project. Tasks 23–25 add final primary allocation, fallback rules and Material
Destination Plan publication as described below.

### 9.4 Stateful primary-destination allocation (Task 23)

`classes/PrimaryDestinationAllocator.py` consumes the final non-direct-tipped
portion of each payload in AWST delivery order, with payload ID breaking equal
timestamps. It uses the same payload-level direct-tip reconciliation as
`MaterialDestinationPlan.build_payload_assignments`, before parent-grade-block
aggregation. Direct-tip tonnes consume no ROM capacity. Payload IDs and full
tonnage precision survive into the assignment and capacity ledgers.

- The confirmed/selected current build instance starts with user-entered remaining
  ROM WMT. Blank remains unresolved; zero advances without assigning tonnes.
- Later instances start with their total 2WP planned ROM WMT across materials,
  unless a remaining-capacity value was explicitly entered for that instance.
  All material lanes sharing one physical instance share a single balance;
  a later turnover at the same footprint has a separate balance.
- A payload with 150 ROM WMT and 100 WMT remaining is assigned whole to the current
  destination. Consumption is 150, overrun is 50, remaining capacity becomes zero,
  and the next payload advances. An Allocate event and an Advance event record
  the payload, instance, before/after capacity and next destination.
- Unconfirmed current instances, missing ROM/material orders, invalid delivery
  times and exhausted sequences retain unresolved tonnes and explicit reasons.
  The allocator does not guess primary or fallback destinations.
- Waste is outside ROM allocation. Deliveries before scenario start or at/after
  the final solved report end consume no capacity. This prevents partial/failed
  plans from consuming future candidate tonnes.
- Each optimised, contingency and manual plan starts independently from frozen
  scenario inputs. Recalculation replaces that plan's audit. Identical retries
  within an allocator are idempotent; changed or out-of-order payloads require
  recalculation from the starting state. Entered scenario capacities are never
  decremented by a plan evaluation.

The final-plan writers call the allocator and atomically publish
`destination_allocation_runs`, `destination_primary_assignments`,
`destination_capacity_ledger` and `destination_capacity_balances`. Tables have
stable schemas in no-data states. A missing/stale/loading reconciliation context
records Unavailable and clears the old audit for that plan. Optimisation restarts
clear old optimised/contingency audits, while source invalidation clears all
derived allocation audits. Scenario database snapshots retain them in `.prj`.

This task produces the post-plan primary assignment audit. Task 24 adds the
fallback candidates described in 9.5, and Task 25 publishes these results in 9.6.
No new submit step is needed in Destination Reconciliation.

Validation: 894 automated checks passed. Native Windows validation also exercised
the scenario input snapshot, manual final-plan hook, whole-payload overrun,
preservation of entered capacity, and actual project save/load of settings and
allocation audit (warehouse inventory reload stubbed).

### 9.5 Destination fallback rules (Task 24)

`classes/DestinationRules.py` indexes the entire imported 2WP guidance schedule.
At APS ingestion, an exact parent-grade-block match keeps the existing nearest
calendar-date, then highest-row-ROM-WMT choice. Saved undated legacy exact ratios
remain supported. When an exact match is missing, fallback candidates supply the
resolved destination. Missing evidence after all fallback searches raises an
explicit unresolved-source error. Legacy dominant-pit and last-stockpile summary
fields alone do not establish evidence; the rules index the actual guidance rows.

Fallback 1 searches these levels in order, excluding the selected primary:

| Order | Required match |
| --- | --- |
| 1 | Pit + stage + bench + flitch + material |
| 2 | Pit + stage + bench + material |
| 3 | Pit + stage + material |
| 4 | Pit + material |
| 5 | Pit + stage + bench + flitch + any non-waste material |
| 6 | Pit area + material |

Mine boundaries remain part of the address. Blast does not participate in this
ladder. Numeric address tokens such as stage `01` and `1` compare equally. Malformed
addresses cannot acquire invented levels. Waste history is excluded using its APS
ore classification and the WS/WASTE material codes. When inventory mappings are
supplied, fallback history must lead to a mapped ROM destination. Within one level,
total historical ROM WMT ranks destinations, then destination name breaks ties.
Evidence records the matched level, total WMT, contributing row count and a
representative grade block. The imported guidance remains the complete row audit.
For level 6, the pit area removes trailing digits from the full pit identifier:
`YOU80`, `YOU02` and `YOU13` share `YOU`. Material still matches its grade-block
code (for example, `SO69` and `SO03` share `SO`). This handles a 24HR pit stage
missing from the 2WP while preserving the priority of the five existing levels.

Fallback 2 ranks measured **stockpile-to-stockpile cycles** from the resolved
primary to another stockpile with the same displayed **Nearest Crusher**. If
there is no primary, its origin is fallback 1, or a mapped original 24HR stockpile
when neither historical role resolves. The CC haul-cycle export contains no
Reserve-to-Stockpile routes; this implementation interprets “nearby” relative to
the destination footprint. It does not substitute a stockpile-to-crusher reclaim
cycle. At both route ends, `:In` takes precedence over an unqualified footprint;
`:Out`, self-routes, missing/non-finite/non-positive cycles and unmapped/different
ROM areas do not qualify. Primary and fallback 1 are excluded from fallback 2.
Shortest cycle wins, with destination name breaking ties. Evidence retains both
route nodes, the origin, ROM area and measured cycle. Missing route evidence leaves
fallback 2 blank with an explicit reason. A **Use** flag controls stockpile feed,
not eligibility to receive ROM tonnes, so an unused build destination can qualify.

If exact, spatial and measured nearby searches all fail, the final resort uses
the latest eligible destination in the **same mine**, across the full imported
2WP horizon. It ranks positive-tonnage non-waste guidance rows by end time
(start time when the end is absent), then start time and file order. A valid mine
address and a date are required; supplied inventory mappings must identify a ROM
destination. This final resort may cross pit areas and material codes within that
mine. Its trace records `last_destination_fallback`, the selected destination,
source block, mine, timestamps, row order and WMT. It does not fabricate an exact
2WP match, spatial match or haul route, or bypass primary build capacity.

Primary, fallback 1, fallback 2 and the two ordered distinct alternates retain
their own fields. Candidate evidence is limited to those reported choices; the
full eligible-candidate count and input signature retain the search context
without duplicating up to 100 rejected routes into every payload. APS grouping preserves this metadata through payload creation. After
Task 23 allocates a final plan, the engine recalculates candidates relative to that
payload's actual primary, so advancing to the next build changes its fallbacks.
The APS payload cache signature includes the destination-rule version, so projects
saved under older rules rebuild their import instead of reusing stale decisions.
The `destination_primary_assignments` audit stores those separate roles, selected
rules, JSON candidate evidence and a rule-input signature. Primary capacity
allocation remains authoritative: fallback candidates do not consume tonnes or
bypass an unconfirmed current instance or a blank remaining-capacity input.

Optimised, manual and contingency plans keep separate audits. New inbound haul
route snapshots persist with their site scenario and `.prj`, and clear/reload with
the haul-cycle input. No extra user controls or Submit action were added. Task 25
publishes the combined capacity and fallback information in Material Destination
Plan as described in 9.6.

Validation on 2026-09-09: 905 automated checks passed. Native Windows validation
imported a haul-cycle fixture, froze scenario inputs, recalculated a manual plan
across a capacity transition, and verified route data and separate primary/fallback
roles through project save/load (warehouse inventory reload stubbed). Existing
loading, cached, no-data and ambiguous-activity states still passed.

A read-only review of the CC 18 August 2WP/24HR exports and
`CC_Cycles_2WP.csv` checked 5,492 source/time combinations from 89 parent grade
blocks: 5,238 exact and 210 spatial resolutions, all 5,448 with a nearby fallback.
Under the original five-level rules, the remaining 44 combinations covered `CC1/HAL03/01/393/130/402/SG17` and
`CC2/YOU80/01/372/001/381/SO69`. These have no eligible spatial fallback in the
original mapping; their generic 24HR HAL_ROM/CC2_ROM origins are also unmapped.
The September 10 edge-case extension resolves YOU80 SO material to
`OPF02_RP01_0201` using YOU13 SO guidance. HAL03 SG uses the final same-mine
fallback to `HAL01_RP01_0304`, supported by the latest eligible CC1 guidance row.
The review is of imported files, not a mutation or rerun of the user's live plan.
Generated screenshots and review records remain local and Git-ignored under
`docs/screenshots/task24/`.

### 9.6 Final Material Destination Plan publication (Task 25)

`classes/DestinationPlanReport.py` publishes the final payload decisions produced
by Tasks 23–24. It uses the existing payload-level direct-tip reconciliation, then
replaces the remaining ROM assignment with the capacity allocator's actual result.
It never presents the original ingestion destination as a confirmed primary when
capacity, current build instance or scenario context is unresolved.

- **Assignments** groups parent grade blocks by assigned destination, physical
  build instance, 2WP order position, status and fallback choices. Repeated builds
  at the same footprint remain separate. Direct-tip and ROM assignments are
  separate rows; fallback columns add no assigned tonnes.
- **Payload audit** retains original sliced grade blocks, payload IDs, AWST
  delivery times, exact quantity buckets, before/after capacity, rule evidence and
  transitions. Assigned, unresolved, waste and outside-window quantities reconcile
  to the payload population. `source_tonnes` is repeated per parent grade block;
  `reported_wmt` and the quantity buckets are additive across rows.
- **Capacity balances** shows each physical build once per plan with starting,
  consumed, remaining and overrun ROM WMT. The summary's before/after values are
  the balance before its first payload and after its last payload; other sources
  can consume the same balance between them. Its consumed capacity is only the
  tonnes assigned by that summary row. Starting capacity must not be added across
  summary rows that reference the same instance.
- **Transitions** shows the Advance ledger events, including zero-capacity skips,
  payload IDs, previous/next destinations and reasons. Whole-payload overruns do
  not split the payload; the following payload advances.
- **Actual movements** retains the exact activity records used for this plan,
  independently of later refreshes. The assignment summary includes detected
  destination, latest inbound, movement count/WMT and user-selected versus detected
  current-instance basis. The run retains activity status, fetched timestamp and
  the AWST request window, including cached/offline provenance and ambiguity.

Publication is one SQLite transaction covering `material_destination_plan`,
`material_destination_plan_payloads`, `material_destination_plan_activity` and the
four allocation/capacity audit tables. Failure rolls back the complete snapshot.
Recalculation replaces only its plan type/ID; optimised, manual and contingency
plans remain independent. Source invalidation clears all derived publications;
optimisation restart clears only optimised/contingency results.

Existing saved MDP schemas migrate in place. Old rows are labelled **Legacy
snapshot — recalculate** and retain their original assigned tonnes/destination.
Opening a saved project does not reconstruct current capacity or overwrite the
plan's old evidence. A newly calculated plan without a valid reconciliation context
shows its confirmed direct-tip tonnes and explicitly unavailable/unresolved ROM
quantities. Empty publications retain stable schemas.

**Results > Reports > Material Destination Plan** provides the five views above,
plan selection, filtering and **Export current CSV**. Selecting an assignment shows
its capacity basis, actual-movement evidence and fallback rules/haul route. Numeric
cells display one decimal for WMT and preserve full precision in tooltips/exports.
**Reload saved results** reads a consistent saved snapshot; it does not recalculate
from changed inputs. Loading and failed-reload states are explicit; a failed reload
can retain only the same scenario's previous snapshot. Late results from another
scenario are discarded. Virtual tables avoid creating a widget for every payload
cell. The existing Manual Blend Plan XLSX includes the updated MDP summary, and the
Database Reports exports expose the detailed audit tables.

Validation on 2026-09-10: 920 automated checks passed, followed by focused checks
of the final report/UI adjustments. Native Windows checks exercised optimised,
manual and contingency selection, the five views, loading, no-data and failed
reload states, and exact publication retention through real `.prj` save/load.
The unrelated warehouse inventory reload was stubbed. Screenshots use illustrative
fixtures and remain local/Git-ignored under `docs/screenshots/task25/`.

Review: restart BlendMaster and recalculate an existing plan, then open **Results >
Reports > Material Destination Plan**. Choose the plan and select an assignment;
compare its primary/fallback rules with **Capacity balances**, **Transitions** and
**Actual movements**. Recalculate after changing Destination Reconciliation inputs.
Task 26 has not started.

## 10. Manual ratio rounding

Task 26 implemented 11 September 2026. **Manual Blending Dashboard > Setup
Blends** has **Round ratios when prepopulating** (off by default) and **Increment**
(default 5%; options 1, 2, 5, 10, 20, 25, 50). These are scenario settings and
apply on the next optimised-to-manual conversion. Existing manual plans are
not rounded again when a control changes or a project is restored.

The rounded path groups each original steady state separately, even when the
optimiser reused a Blend ID. It rounds full physical-feed WMT shares across
stockpiles and direct-tip sources together; these sum to 100%. Source order is
the report's stable order. Whole-percent and increment ties round half-up;
earlier sources retain their snapped shares, later sources absorb overshoot,
and the first positive source receives residual increment units. Zero original
sources are never introduced by rebalancing.

Each recipe is replayed using its original physical feed rate. Mapped crusher
and product rates, additive properties, grades and build outcomes are calculated
from the new physical withdrawals. Inventory/chunk and product-build boundaries
are recalculated; depletion-controlled recipe ends may move, and subsequent
contiguous recipes are recalculated within the original planning horizon.
Calendar boundaries and explicit gaps remain absolute. Direct-tip eligibility
is rechecked in each recalculated window. A direct-tip shortage first shortens
the recipe at the same rounded ratios and physical feed rate. Delivery times
are rechecked until the shorter window fits every required source. Feasible
states already completed before a build/chunk boundary are retained if no
further duration fits. Subsequent contiguous recipes move earlier where
calendar anchors permit; an unfilled tail is reported in the completion message.
The timing adjustment appears in Ratio Rounding Audit and PDF/XLSX outputs.
If no positive direct-tip duration fits (including after a build/chunk boundary),
retry that recipe from its opening stock with direct tip removed. Split the total
rounded grade-block share into equal percentage-point additions across all
stockpile sources listed in that recipe, including any rounded to zero. Round
the redistributed shares again to the selected increment, using the original
source order to resolve ties and retain a 100% total (52.5/47.5 becomes 55/45
at a 5% increment). Recalculate depletion,
product-build boundaries, grades and downstream timing using the resulting mix.
Attempts are isolated: discarded attempts cannot consume stock or advance build
progress in their replacements. If both attempts fail, retain the longest valid
contiguous portion from one attempt, plus earlier accepted recipes, then stop;
never combine overlapping attempts or skip ahead to later recipes. If no states
are valid, leave the previous plan unchanged. The completion message, Blend Plan
notice, rounding audit and PDF/XLSX outputs identify a partial sequence and its
stopping reason; this metadata survives save/load and unchanged submission.
No fallback invents inventory or applies optimiser grade constraints to manual
blending. Explicit reclaim-only fallback is the only step that changes the mix.

Successful results become fixed, editable manual starting states, preserving
exact source tonnes and subsecond timing for restoration. **Results > Reports >
Blend Plan > Ratio Rounding Audit** shows original/rounded shares and original/
recalculated timing, including sources rounded to zero. Raw manual reports retain
rounding provenance; the PDF and XLSX exports include the conversion audit.

- Optional. When disabled, exact optimiser ratios prepopulate (Q13).
- Applies to both stockpile blend ratios and direct-tip acceptance ratios
  (Q13).
- Two-step: round to nearest whole number, then snap to the configured
  increment. With a 5 percent increment, 43.6 becomes 44 then 45 (Q13).
- Rounding is applied once per steady state. Ratios must sum to 100 percent;
  where ordering matters, round the first source first (Q14, Q15).
- Outputs must be recalculated from the rounded inputs so reports agree with
  the published ratios (Q13).
- Raising a source ratio can move a steady-state boundary when that boundary
  was triggered by the source running out. Affected downstream steady states
  must be detected and recalculated (Q14).
- The manual path has no constraints of its own and may breach optimiser
  constraints. Existing target-versus-actual reports let the user check before
  publishing (Q16).

## 11. Backup destinations

Task 27 implemented 11 September 2026. **Results > Reports > Blend Plan** has a
backup selector for each configured tipping point. Choices are the active manual
plan's saved Material Destination Plan fallback 1/2 destinations, restricted to
stockpiles whose current Nearest Crusher mapping matches that tipping point.
No new fallback search or physical allocation is performed by this selector.
For the existing Total_Feed mode, explicitly configured APS crusher choices
identify the tipping points for these publication instructions; Task 28's
simultaneous lane refactor has not started.

Choices are owned by the manual plan (including contingency plans), are saved
with the scenario/project, and are rechecked at export. If choices exist, a
selection is required before publishing. An obsolete saved choice remains
visibly unavailable until corrected/cleared. If no eligible fallback exists,
the published instruction explicitly says **No eligible fallback**. The PDF
and XLSX publish the backup instruction for all trucks for the whole plan.
Selecting a backup does not alter the Material Destination Plan or consume
capacity. Clearing a manual plan also clears its backup choices.

Tasks 26–27 validation: the 966-test regression suite passed, followed by 16
focused checks after the final rollback and gap-preservation adjustments. Native Qt checks
exercised exact/rounded prepopulation, backup selection, no-data/stale-choice
states and actual `.prj` save/load. The application's PDF/XLSX exporters were
run; new PDF sections and workbook sheets were read and rendered for visual
review. Tests used illustrative data and isolated databases, with warehouse
reload, the Gantt web server and unrelated destination recalculation stubbed.
Screenshots/exports and the short review guide remain local under
`docs/screenshots/task27/` and `docs/TASK_26_27_UI_REVIEW.md`. Stop after Task 27.

- Chosen per tipping point for the whole plan at the Blend Plan tab, not per
  period or steady state (Q17).
- Selected from the already-resolved fallback destinations (Q17).
- Applies to every truck feeding that tipping point, including direct-tip
  blocks. It is published plan metadata; app logic continues to use fallback
  destinations (Q18).

## 12. Multi-tipping-point and multi-OPF ownership

Mode definitions (Q21):

- Total_Feed multi-crusher: several crushers feeding one OPF.
- Combined OPF: several OPFs, each with at least one crusher.

Contracts:

- Whenever more than one crusher exists, each crusher carries its own full set
  of grade targets, constraints and properties (Q21).
- Crusher grade constraints are evaluated at tipping time. Product-build grade
  constraints are evaluated at OPF-arrival time after conveyor and COS delays
  (Q30).
- All tipping points feeding one OPF share the OPF brand, because brand is an
  OPF property (Q22).
- A product build may be fed by one OPF or several. Product Targets rows gain
  an OPF property in combined-OPF mode so builds can be shared or separated
  (Q21, Q23).
- A single-OPF build behaves as today. A multi-OPF build must hit target on
  combined output while crusher-level constraints still apply (Q23).
- OPFs helping each other means one combined build target, with relaxed crusher
  targets permitting off-spec feed at some tipping points provided the overall
  build stays on target (Q24).
- Support at least three OPFs even though sites currently have at most two
  (Q23).
- A stockpile feeds only one tipping point at a time, normally its Nearest
  Crusher. Cross-mine transfers are modelled with a new editable `Subset`
  column in Stockpile Inventories, prepopulated from Nearest Crusher, exposed
  in Decision Levers as Rehandle Movement Rules (Q19).
- Max Reclaim Rate is per stockpile x crusher route (Q20).
- Historical reconciliation stays applied per OPF and brand before streams
  combine (Q25).

## 13. AMT tonnage safety and footprint exclusion

- Positivity is decided by the sum of raw hex `RAW_WMT`, excluding
  `UNATTRIBUTED_MOVEMENT_WMT` (Q46).
- When the AMT footprint total is at or below zero and inventory is positive,
  all final hex and footprint tonnes become zero and the positive inventory
  balance is not allocated back into the footprint. The guard runs before
  inventory scaling and allocation (Q47).
- When inventory is at or below zero, zero the footprint. For a positive raw
  footprint with inventory unavailable, retain spatially reconciled AMT tonnes
  (Q48). A non-positive raw footprint always stays zero.
- See [AMT tonnage reconciliation](AMT_OPENING_HEXES_AND_GRADE_BLOCK_LINEAGE.md)
  for snapshot migration and downstream chunk/solver rules.
  Raw evidence and the zeroing reason survive persistence; zeroed material
  cannot re-enter through cached chunks, inventory fallback or solver events.
- Whole-footprint exclusion is implemented in AMT setup; see
  [Whole-footprint exclusion](AMT_OPENING_HEXES_AND_GRADE_BLOCK_LINEAGE.md#whole-footprint-exclusion).
- Excluded footprints are skipped before Snowflake and AMT processing and do
  not appear on reports (Q49, Q50).
- If every footprint is excluded, the user may proceed when at least one
  conventional inventory stockpile is selected, and must be blocked otherwise
  (Q50).
- Participation decisions and the exclusion audit persist per scenario/project.
  Restoring a footprint requires a refresh and fresh chunks. Changing inclusion
  invalidates generated scheduling reports, while preserving input data and
  unrelated user tables.

## 14. Conveyor and COS latency (Phase 4)

Deferred to Phase 4 by agreement, so it lands after Phase 1 to 3 are validated
and does not destabilise the recent dynamic steady-state fix in
`classes/Optimizer.py` (Q26).

Contract for when it is built:

- Optional feature. COS capacity and conveyor capacity are WMT properties of a
  crusher, plus a COS chunk count.
- Filling and depletion are FIFO.
- At model start, reconstruct conveyor and COS contents from grade blocks
  tipped into that crusher, looking back
  `COS capacity / crusher rate + conveyor capacity / crusher rate` hours.
- COS behaves as continuous flow: the earliest chunk depletes into the product
  build while a new chunk fills concurrently, both at crusher rate.
- A grade block moved in BlendMaster reaches the COS after the conveyor
  transit time and then fills the active chunk.
- COS chunk boundaries trigger steady states. Conveyor transit does not; it is
  latency on payloads tipped at the crusher.
- When a crusher has a conveyor, stockpile feed also becomes payloads using
  rehandle payload, spot time in seconds and dump time in seconds, exposed in
  Decision Levers only when conveyors are modelled.
- The model stops at the schedule end with no extension. New SQLite reports
  cover COS chunk opening and closing tonnes and grades (Q29).
- An animated COS profile with a time slider showing chunk fill, depletion and
  per-chunk grade-block composition is desirable.
- Questions 27 and 28 are superseded by this model.

## 15. Recorded gaps requiring change

| Ref | Gap | Lands in |
| --- | --- | --- |
| Q1 | Stockpile-only ROM build order and material grouping implemented in the dedicated extractor | Task 20 complete |
| Q7 | Activity detection and setup review implemented; payload allocation pending | Tasks 21–22 complete; Task 23 pending |
| Q32 | No spatial factor resolution layer; factors applied globally | Task 5, 6, 7 |
| Q32 | Daily reconciliation SQL does not retain contributing grade blocks | Task 5 |
| Q42 | No confidence or uncertainty metric | Task 6, 8 |
| Q43 | No grade-block lineage for inventory stockpiles | Task 5 |
| Q52 | No LQL/HQL fields on product-build rows | Task 12 |
| Q57 | Hard/Soft configuration and Soft execution implemented | Tasks 16–17 complete |
| Q59 | Configurable linear and increasing piecewise-linear penalties implemented | Task 17 complete |
| Q19 | No stockpile `Subset` column or Rehandle Movement Rules | Task 28 |
| Q20 | Max Reclaim Rate not per stockpile x crusher route | Task 28 |
| Q21 | Product-build rows have no owning-OPF property for combined mode | Task 29 |
| Q26 | No conveyor or COS model | Task 30 |

## 16. Deferred by agreement

- Conveyor and COS latency, Phase 4 (Q26).
- Imported LQL/HQL; manual entry only for now (Q54).
- Analytes beyond the existing five in the production report (Q69).

## 17. Invariants for later tasks

1. Grade-block identity stays the six-token operational key. Do not reintroduce
   `Reserves` or mine tokens.
2. Advanced reconciliation keys on the parent grade block, never the APS slice.
3. Physical depletion and opening/closing balances stay ROM WMT regardless of
   selected grade stream.
4. Grade comparisons use the selected stream on both sides.
5. Global reconciliation remains the terminal fallback and must stay reachable.
6. Sources without lineage keep global factors and must not be silently
   dropped.
7. The solver stays linear. No contract may require a quadratic objective.
8. Hard mode behavior is frozen; soft mode is strictly additive.
9. Excluded AMT footprints must not reach Snowflake, processing or reports.
10. The non-positive footprint guard runs before inventory allocation.

### Manual sequence handover correction (11 September 2026)

- Imported exact and rounded states retain exact boundaries and per-source tonnes through sequence hydration, reload and unchanged submission. The one-decimal duration is display precision only; Remaining Hrs uses the preserved exact interval and tolerates the six-decimal maximum-duration storage precision.
- Real schedule edits invalidate fixed-state quantities and trigger normal manual recalculation. Rejected submissions retain the previously accepted sequence metadata. Remaining Hrs is derived and read-only; refreshing it does not emit user-edit callbacks.
- Validation: 148 focused checks passed, including depleted inventory with direct tip, repeated hydration, real edits, rejected submission, Qt signal handling and exact microsecond overlap boundaries. Isolated native application checks passed for exact and rounded prepopulation, reload and submission with preserved tonnes. Task 28 remains unstarted.

Direct-tip duration fallback validation (11 September 2026): 160 focused checks passed. Native rounded prepopulation, submission, actual project save/load, PDF and XLSX exports passed using limited direct-tip evidence. Delivery exclusion, multiple required sources, calendar anchors, product-build boundaries and a retained feasible portion of a recipe are covered. Task 28 remains unstarted.

Reclaim-only and partial-sequence fallback validation (11 September 2026): 169 focused checks passed. Isolated native prepopulation and submission passed for both outcomes, including the persistent partial-plan notice, actual project save/load, and PDF/XLSX outputs. Equal percentage-point redistribution, unchanged rounding increments, recalculated depletion, isolated retries, retained valid states and report-construction failures are covered. Task 28 remains unstarted.

Post-redistribution rounding validation (11 September 2026): 89 focused checks passed, including 52.5/47.5 becoming 55/45, recomputed depletion and grades, and all supported increments. Native prepopulation/submission, project save/load and PDF/XLSX outputs passed with 55/45 final ratios. Task 28 remains unstarted.
