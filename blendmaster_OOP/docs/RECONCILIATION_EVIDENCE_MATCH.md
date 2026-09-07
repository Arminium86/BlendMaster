# Source-directed spatial reconciliation and evidence match score

Implemented 6 September 2026 as a clarification of Tasks 6–8A, before Task 9.
The user confirmed that Spatial should look up the most relevant historical
context for the composition of the source being adjusted, and requested
**Evidence match score** in place of confidence, with no uncertainty display.
Updated after Task 11 on 6 September: Auto now independently compares all five
spatial levels within each candidate window. See
[the level-search extension](RECONCILIATION_AUTO_LEVEL_SEARCH.md).
Updated 7 September: Auto also compares shared whole-source histories with the
component-based candidate. The current complete mental map and validation are in
[the shared-history extension](RECONCILIATION_SHARED_HISTORY.md).

## The three advanced methods

| Method | Historical selection |
| --- | --- |
| Advanced · spatial and compositional | Rank spatially eligible shifts by their whole-feed match to the whole inventory/hex composition. Keep the highest-matching shifts needed to meet the minimum distinct production dates, including all ties. |
| Advanced · lookback window | Keep all eligible spatially matching shifts inside the chosen Calendar, Production days or Latest campaign window. No match-based trimming occurs. |
| Auto · maximise evidence match score | Compare component-based and shared whole-source histories at every eligible spatial level within Spatial horizons and supported lookback windows. Choose the best approach/method/window per physical source and brand. |

Standard global reconciliation remains available and is the default.

Spatial now differs from a wider Lookback. Its selected shifts can be separated
by days of less relevant production. Their chronological order and factor values
do not determine their match ranking. Lookback can still win because its time
window can exclude a different mix of intermediate-match shift feed tonnes.

## Spatial selection rules

1. Use the existing validated paired blend/regression shift history for the
   requested OPF and brand, completed before scenario start. Retain per-analyte
   maximum calendar lookback bounds and valid positive factor requirements.
2. Compare each historical shift's complete feed composition with the entire
   known source composition using the existing six-level spatial/material
   overlap score. This includes all co-fed blocks, so unrelated shift feed
   lowers the score. Unknown source mass remains accounted for in the final
   physical-source score rather than being redistributed.
3. For each lineage component, use the first existing fallback level where all
   ten factor series have enough distinct production dates. The common-level
   requirement and fallback order remain unchanged.
4. Rank eligible shifts by their whole-source match score. Determine the highest
   common score cutoff that supplies every series with its required distinct
   production dates. Retain all eligible shifts at or above it, including all
   equal-score ties. Scores are rounded to ten decimal places for ranking.
   Per-series validity and local maximum bounds still apply.
5. Apply the usual total-period-feed WMT weighting to selected factor values.
   The match score does not multiply or reweight those values. Local manual
   factors apply afterward, then physical lineage fractions combine factors
   into a hex or whole inventory stockpile.

The selection is a deterministic prefix of source-match-ranked shifts. It is
not an exhaustive search over every possible subset of shifts. Including all
ties prevents an arbitrary choice between equally relevant shifts and can yield
more production dates than the minimum. The minimum counts dates, not shifts,
and does not require consecutive dates. Every component uses the complete
source as its match reference; component cutoffs can differ where their eligible
history or local guardrails differ.

No spatial evidence meeting the bounds still results in global fallback.
Missing lineage contributes zero match score for its physical WMT; zero-WMT
sources remain unscored. No factor is invented, and APS is unchanged.

## Auto and the review

Auto retains one method/window policy per inventory or hex and brand. The full
maximum-lookback ordinary Spatial result (first sufficient level) remains the
comparison baseline. Auto evaluates all five levels within that horizon and
every temporal candidate, including levels broader than an already-sufficient
level. One level must support all ten series in each component. Spatial candidates
rank shifts separately at each level; Lookback candidates retain all eligible
shifts in their window at each level. Temporal candidates use the same minimum/maximum
guardrails. Temporal membership caches distinguish Spatial ranking from Lookback
selection even when both methods can see the same periods.

All levels use the same six-resolution composition overlap score. There is no
extra score deduction for selecting a broader level. Specificity breaks score
ties. Standard global factors remain the terminal fallback if no spatial level
qualifies, and missing lineage keeps zero score. The component review lists all
five candidates, their eligibility and scores, and the selected level.

**Spatial · 30 days max** means that source-matched shifts were selected from
within a 30-day horizon. It does not mean all 30 days were used. Component
evidence reports selected versus eligible period counts, the included match
cutoff, production dates, period feed WMT and the actual timestamps. **Calendar ·
7 days** retains the fixed seven calendar dates before scenario start, subject
to the maximum bound; it does not move a seven-day block around inside 30 days.

Auto's displayed improvement is relative to the new source-matched Spatial
baseline. Earlier Task 8A results used the former all-history Spatial baseline
and should not be compared as though the selection rule were unchanged.

Local factors and windows supports both inspection and editing. **Review source
/ hex** now matters in Spatial as well as Auto because source compositions can
produce different automatic factors for the same cell. Local overrides still
apply by OPF, brand, spatial cell, material type and analyte across sources.

## Terminology and compatibility

- Data Streams uses **Evidence match score**, **Sources and evidence**, and
  **Auto · maximise evidence match score**. Uncertainty is removed from source
  rows, component rows and the overall summary.
- Detail text, help, manual-edit notes, background status/error text and review
  CSV use the new terminology. CSV methods use their readable dropdown labels.
- Database View and CSV expose `recon_<brand>_evidence_match_score_pct`,
  `recon_<brand>_baseline_evidence_match_score_pct` and
  `recon_<brand>_evidence_match_score_gain_pp`. No uncertainty column is emitted.
- Legacy flattened field names and saved column choices are translated at the
  display boundary, preserving numbers and selections while dropping the old
  uncertainty field. New names take precedence if both versions are present.
- Existing stored audit keys and method IDs remain readable (`confidence_percent`,
  `uncertainty_percent`, `auto_max_confidence`) to preserve project/database
  compatibility. They are internal compatibility names rather than current UI
  labels. Older manual-edit notes are also rendered using the new wording.
- Source-dependent selection caches include the match ranking and requested
  spatial level, so shared cells cannot reuse another source's chosen history
  or another level's selection. Algorithm revision 4 invalidates
  derived application/review/AMT enrichment caches without forcing a new history
  read. Auto search provenance is version 4; component-based provenance retains
  the `whole_source_match_ranked_shifts` rule, threshold and counts. Winning shared
  sets also record their common membership, eligibility and scoring rules.

The separate EXPIT sequence geometry/replay reliability classification retains
its existing terminology because it is not the grade-factor match metric.

## Original source-selection validation (before the level-search extension)

**644 tests passed**, including ten new tests for ranked source selection,
non-consecutive dates and ties, shared cutoffs with local guardrails, distinct
Spatial/Lookback caches, a Calendar-winning Auto example, mixed-source/chunk
gain aggregation, algorithm cache invalidation, old-audit display and saved
Database View field migration. An independent shift-prefix oracle checks twelve
varied histories; the existing exhaustive window oracle checks ten more.
Existing tests were updated for the intentional selection/label changes. The
two pre-existing pandas warnings remain in CaseModeller and DrawCharts.

Native Qt Spatial, Auto and local-settings screens were rendered with installed
Segoe UI fonts and visually inspected. Database View was checked with the new
score fields and an example where Auto chooses Calendar.

On the saved Task 7 extract (BIG01 inventory and 44 positive KAN AMT hexes), with
minimum production days **3** and maximum lookback **30**:

| Mode | Overall evidence match score | Local calculation time |
| --- | ---: | ---: |
| Lookback, trailing 7 calendar days | 21.942450451% | 0.53 s |
| Source-directed Spatial | 30.183248992% | 0.44 s |
| Auto | 30.183263734% | 2.29 s |

All modes retained **129,904.43232 WMT**. These are results on the same fixed
extract and guardrails, not measurements of prediction accuracy. Auto's gain
over Spatial on this extract is very small; both display as 30.2% in the UI.
Validation reused saved data and temporary artifacts without production writes.

Restart BlendMaster and calculate the review again to load the current selection
logic and labels. Submit then applies the new factors through the existing
workflow. The original validation above predates Tasks 9–11; current extension
validation is recorded in [the level-search note](RECONCILIATION_AUTO_LEVEL_SEARCH.md).
