# Task 6 — Deterministic reconciliation factor resolution

Historical implementation record. The user's 6 September 2026
[source-matching clarification](RECONCILIATION_EVIDENCE_MATCH.md) supersedes the
Spatial selection rule below: it now ranks shifts by the complete source's
match, and the visible metric is Evidence match score with no uncertainty field.

Implemented in `classes/ReconciliationFactorResolver.py`. This is a pure resolver
over Task 5 history and the existing effective standard factor record. It returns
factors, supporting evidence and confidence at lineage-component and source grain.
Grade application is Task 7; controls and review UI are Task 8.

The Q32/Q40 clarifications in `AUTHORITATIVE_DATA_AND_BEHAVIOR_CONTRACTS.md`
supersede the original Task 6 hierarchy and independent-analyte wording.

## Selection and weighting

For each parent grade-block component, search in this order:

1. pit + stage + bench + blast + flitch + material type
2. pit + stage + bench + blast + material type
3. pit + stage + bench + material type
4. pit + stage + material type
5. pit + material type
6. Supplied effective global OPF/brand/analyte factors

All five analytes and both factor kinds must meet the minimum production-day
requirement at **one shared spatial level**. Missing analytes may use different
valid periods within that level. Two shifts on one production date count as one
day. Non-positive, missing and non-finite factors do not contribute evidence.

A shift qualifies if any positive attributed feed matches the selected cell. Its
factor represents the complete OPF/brand/shift, so both blend and regression
aggregation weight by **total shift feed WMT**. Matching-block tonnes and product
tonnes do not replace that weight. Several matching blocks and the paired factor
kinds cannot double-count a shift.

Canonicalisation uses the existing parent-block parser, including numeric token
normalisation and removal of APS slice suffixes. Material type is the leading
letters of the final parent-block token. Another material type never becomes a
spatial fallback. History remains scoped to one OPF and brand; an exact brand or
unique suffix alias can select it. Ambiguous aliases use the global fallback.

Missing lineage remains an explicit global component with its physical source
fraction. The resolver requires all ten effective standard factors and does not
invent a terminal 1.0. A 1.0 already supplied by the standard service is preserved,
as are effective manual overrides and the standard record's provenance.

## Methods and guardrails

| Parameter | Default | Meaning |
| --- | --- | --- |
| `method` | `spatial_compositional` | `standard`, `lookback`, or `spatial_compositional` |
| `max_lookback_days` | 30 | Maximum elapsed calendar days before scenario start |
| `min_production_days` | 1 | Minimum distinct production dates for each of the ten factor series |
| `window_mode` | `calendar_days` | Applies to the `lookback` method |
| `lookback_days` | 7 | N for the selected lookback mode |

`standard` returns the supplied global factors without inspecting history.
`spatial_compositional` searches all eligible periods inside the maximum lookback.
`lookback` first restricts that history to one of:

- `calendar_days`: the previous N completed production dates, excluding the
  scenario calendar date. A prior date's Night shift can end at 06:00 on the
  scenario date. All shifts must also be complete by scenario start.
- `production_days`: the latest N dates with positive, paired production history
  for the brand, allowing gaps between dates.
- `latest_campaign`: the final N dates from the latest consecutive run of dates
  containing that brand. A missing calendar date ends the run; a missing shift
  does not. **This is an implementation assumption because history has no unique
  campaign ID.** The CBFL campaign marker is not a unique campaign identity.

Production dates come from period start in Perth time. The maximum lookback also
bounds the production-day and campaign modes. Selected windows never expand to
replace invalid or spatially unrelated evidence; selection instead proceeds down
the spatial ladder and then uses the supplied global record. Automatic window or
confidence optimisation remains deferred. The terminal standard record retains
its own existing lookback; advanced limits do not recompute that global record.

## Confidence definition

The clarifications prescribe the score's direction and endpoints, but no formula.
Task 6 implements the documented diagnostic
`hierarchical_composition_overlap_v1`. **It is a composition-relevance score,
not a calibrated probability, grade-error estimate or statistical confidence
interval.** Its `uncertainty_percent` is the complementary score, `100 - confidence`.

For each selected period, compare the complete source composition with the
complete period feed composition at six nested partitions: exact canonical
parent grade block, then each of the five spatial/material levels above.
At partition j, calculate histogram intersection:

```text
overlap_j = sum(min(source_fraction[cell], history_fraction[cell]))
period_similarity = 100 * mean(overlap_1, ..., overlap_6)
```

Equal weight for the six partitions is an explicit implementation choice. An
identical parent composition scores 100. For a single-block source, a different
parent in the same flitch/material cell scores 83.333; a match only at pit/material
scores 16.667. For blocks in unrelated pits/materials, a 40/60 source versus a
60/40 historical mix scores 80. The formula measures hierarchical address
similarity, not physical distance in metres.

History fractions use total period feed, so unknown or unrelated historical feed
reduces similarity. Source fractions in this comparison condition on attributed
source tonnes. Each factor's score is the period-feed-weighted average over the
same valid periods used for that factor. The component's reported confidence is
the **minimum of the ten factor scores**, a conservative implementation choice
when analytes have different valid histories. The per-factor scores remain in
the audit record.

Whole-source confidence weights component scores by physical source fraction.
An explicit unknown-source component scores zero, penalising missing source
lineage exactly once. For example, 75% known source lineage with a perfect
historical match yields 75% source confidence, not 100% or 56.25%. Global fallback
scores zero because there is no accepted spatial evidence; this does not imply
the supplied global factor is known to be wrong. Standard mode uses the same
zero score with an explicit statement that spatial confidence was not assessed.

`aggregate_source_confidence` combines source results by source WMT for AMT chunk
or overall reporting. An unscored positive-tonne source retains its weight with
zero evidence; zero total tonnes returns `None` for confidence and uncertainty.

## API and audit

Instantiate once per OPF/brand/configuration, then reuse across sources:

```python
from classes.ReconciliationFactorResolver import (
    ReconciliationFactorResolver, aggregate_source_confidence,
)

resolver = ReconciliationFactorResolver(
    samples,
    opf="CB OPF", brand="SF", scenario_start="2026-08-22 06:00:00",
    standard_factors=global_factors["SF"],  # single brand, effective factors
    method="spatial_compositional", max_lookback_days=30, min_production_days=3,
)
source = resolver.resolve_source(
    inventory["build"], "inventory",
    inventory["contributing_blocks"], inventory["inventory_wmt"],
)
# For AMT, use source_kind="amt" and supply hex_id plus that hex's ROM WMT.
component = resolver.resolve(
    "PIT01|1|453|426|456|LG01", source_id="example", source_kind="inventory",
)
overall = aggregate_source_confidence([source])
```

`resolve_source` returns separate factor records and physical lineage fractions;
it does not average or apply the factors to grades. `resolve` defaults to a
single-parent reference composition. When resolving one component of a mixed
source directly, also supply `source_composition` and `source_wmt` for its score.

Each record uses the Task 3 resolved-factor schema, with an optional v1
`provenance` mapping (default `{}`). It contains the shared level, factor maps,
selected spatial key, distinct supporting periods and sample IDs, original row
counts, total and matching feed WMT, actual supporting date span, fallback reasons,
per-factor production-day counts/weights/confidence and requested configuration.
Global records preserve the full standard record instead of fabricating shift
weights or counts. The actual supporting span is distinct from the requested
window recorded in provenance.

Duplicate samples, overlapping periods, incompatible schema versions and paired
factor kinds with inconsistent feed composition are rejected. Unpaired or
non-positive-feed periods are excluded with reasons. Missing positive source
lineage is retained as unknown. Tiny floating-point tonne overshoots are corrected
within tolerance; materially excessive lineage tonnes are rejected.

Canonical identities and spatial selection are indexed/cached within the resolver.
Composition similarity is evaluated at most once per relevant period per source,
then reused across its components. Scores are never shared between sources.
Use a new resolver when configuration or history changes. Resolving sources issues
no queries and changes no grades, SQLite records, solver state or UI.

## Validation

From `C:\BlendMaster\blendmaster_OOP`:

```powershell
python -m unittest tests.test_reconciliation_factor_resolver -v
python -m unittest discover -s tests
```

- 41 resolver tests cover all six levels, weighting, missing analytes, fixed
  windows, production dates, campaign gaps, incomplete lineage, confidence,
  canonicalisation, global preservation, invalid inputs and cache isolation.
- Full suite: **542 tests passed**, with the two existing pandas warnings.
- Live read-only check: 30 days ending 22 August 2026 at 06:00 Perth, 120 CBSF
  samples representing 60 shifts; 36 existing history warnings retain incomplete
  historical lineage. Two positive opening builds supplied 178 components with
  no opening-lineage warnings. Every resulting factor was independently
  recalculated from the raw samples identified by its audit record.
- The spatial method, minimums of one and three production dates, and all three
  seven-day lookback modes were exercised. These particular live components all
  selected the most specific level; broader levels are covered by offline cases.
- Live records serialize as strict JSON, and component fractions retain total
  physical source mass. No warehouse or application database records were written.
- Final source-level cache check: index construction took 0.157 seconds and
  resolution of all 178 components took 0.131 seconds, excluding warehouse reads.
  Only 120 period/source similarity evaluations were needed. All component scores
  also matched independently requested single-component results to 1e-12.

Observed live source scores (diagnostic, not grade-prediction probabilities):

| Inventory build | Opening WMT | Spatial, 30 days | Lookback, 7 days |
| --- | ---: | ---: | ---: |
| BIG01_RP01_0007_26005 | 124,780.28 | 15.612% | 22.150% |
| KAN82_RP01_0003_26001 | 5,124.16 | 26.550% | 35.632% |
| WMT-weighted overall | 129,904.44 | 16.043% | 22.682% |

All three lookback modes happened to select the same seven dates in this extract.
The narrower window's greater composition relevance illustrates why the selected
spatial level alone cannot establish a high-confidence source adjustment.

Task 6 stops at factor resolution and confidence. Task 7 has not started.
