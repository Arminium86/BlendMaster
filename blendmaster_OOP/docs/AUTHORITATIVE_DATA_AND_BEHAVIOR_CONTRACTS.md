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
   supported lookback families.

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

Auto selects one method/window policy per physical inventory stockpile or AMT
hex and brand, scored against that source's complete composition. The search
covers whole-day spatial horizons, trailing completed calendar dates, last N
brand production dates, and final N dates of the latest consecutive production
campaign. Spatial candidates apply the source-match ranking rule; lookback
candidates keep all eligible periods in their window. This is a deterministic
ranking-prefix search, not unrestricted optimisation over all shift subsets. Minimum
production days and maximum calendar lookback are user guardrails, including
local cell/analyte settings. Saved manual window choices are retained for the
other methods; Auto chooses the method and N within those guardrails.

Each component retains the first eligible shared fallback level for all ten
factor series. Manual factors apply after selection without increasing the evidence
match score. The full maximum-lookback source-matched spatial result is the comparison baseline
and a candidate. Ties prefer less global evidence, finer fallback levels, more
supporting production dates, then more period feed, with deterministic policy
order resolving remaining ties. Score differences below numerical precision
(scores rounded to ten decimal places for ranking) are treated as ties.

Fixed physical source WMT makes these independent choices maximise the existing
WMT-weighted overall score within the supported search space. Missing lineage
remains in the denominator with zero evidence match score. AMT chunks aggregate hex
choices when available; before chunking, footprints show a preview. Selected
windows, baseline evidence match score and gain in percentage points are retained in
source/chunk audits, the review and CSV/Database View fields. See
[the source-matching clarification](RECONCILIATION_EVIDENCE_MATCH.md) for the implemented scope.

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
- When the selected advanced window yields no valid factor, resolution walks
  the section 4.2 ladder rather than expanding the window.
- When every spatial level is exhausted, the terminal fallback is the standard
  global OPF/brand/analyte factor, never 1.0 and never a blocked submission.
- Minimum production days and maximum lookback window bound the search (Q42).

### 5.4 Evidence match score (Q32, Q42, Q45; terminology clarified 6 September)

- Evidence match score is 100 percent when every grade block in the source period has the
  highest spatial and compositional relevance to the source being adjusted, and
  the composition ratios match.
- The score decreases as ratios deviate, as fallback levels are used, and as
  less relevant material enters the same feed period.
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

## 8. Product Targets, LQL/HQL and target modes

Current behavior: `setup/PlanningPlanTargets.py` builds product-build rows with
`build_id`, `brand`, `byproduct`, `cbfl_campaign`, `target_tonnes`,
`planning_target_tonnes`, `crusher_contribution_ratio`, `opf`, `crusher` and
planning period fields. Grades arrive as `fe`, `si`, `al`, `p`, `mn`. 2WP gives
an Fe lower bound and contaminant upper bounds, leaving opposite bounds open.

Contracts:

- The tab renames from Product Build Settings to Product Targets (Q11 of the
  task list, Task 11). The caption lives at `GUI/InitialiseGUI.py:2992`.
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
- Three displayed decimals apply only to 2WP-imported product targets;
  calculations keep full precision and everything else is unchanged (Q51).

### 8.1 Penalty formulation

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

Evaluation basis is user-selectable between per-steady-state product output and
cumulative active build average, and applies whether or not the build completes
within the horizon (Q61, Q62).

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

## 10. Manual ratio rounding

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

- Positivity is decided by the sum of raw hex `RAW_WMT` (Q46).
- When the AMT footprint total is at or below zero and inventory is positive,
  all final hex and footprint tonnes become zero and the positive inventory
  balance is not allocated back into the footprint. The guard runs before
  inventory scaling and allocation (Q47).
- When inventory is at or below zero, zero the footprint. When inventory is
  unavailable, retain spatially reconciled AMT tonnes (Q48).
- Excluded footprints are skipped before Snowflake and AMT processing and do
  not appear on reports (Q49, Q50).
- If every footprint is excluded, the user may proceed when at least one
  conventional inventory stockpile is selected, and must be blocked otherwise
  (Q50).

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
| Q1 | Destination-type stockpile filter and per-material-type ordering absent from guidance builder | Task 20 |
| Q7 | No planned-versus-actual destination order reconciliation | Task 21, 23 |
| Q32 | No spatial factor resolution layer; factors applied globally | Task 5, 6, 7 |
| Q32 | Daily reconciliation SQL does not retain contributing grade blocks | Task 5 |
| Q42 | No confidence or uncertainty metric | Task 6, 8 |
| Q43 | No grade-block lineage for inventory stockpiles | Task 5 |
| Q52 | No LQL/HQL fields on product-build rows | Task 12 |
| Q57 | No hard/soft target mode | Task 16 |
| Q59 | No piecewise-linear deviation penalty | Task 17 |
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
