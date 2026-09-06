# Task 7 — Apply advanced factors to inventory and AMT grades

Historical implementation record. See the 6 September 2026
[source-matching clarification](RECONCILIATION_EVIDENCE_MATCH.md) for current
Spatial selection and the Evidence match score terminology used in the UI/CSV.

Task 7 connects the Task 6 resolver to the existing grade-stream pipeline.
Inventory stockpiles receive one adjustment at their whole-stockpile average;
AMT receives an adjustment for each hex before scheduling chunks are formed.
This follows the Q32/Q39/Q42/Q43 clarifications, including their confirmation
that inventory stockpiles participate in advanced reconciliation.

**Standard remains the default.** Task 7 introduces the application and state
interfaces; selectable controls and the factor/confidence review UI are Task 8.
No new controls or screens have been added.

## Grade calculation

For source component j, let w_j be its final physical ROM WMT divided by total
source ROM WMT. Each component uses the shared spatial level and factor set
resolved by Task 6. For each analyte:

```text
source_blend_factor      = sum(w_j * component_blend_factor_j)
source_regression_factor = sum(w_j * component_regression_factor_j)

adjusted_rom     = mapped_modelled_rom * source_blend_factor
adjusted_product = mapped_modelled_product * source_regression_factor
```

For example, a 40/60 source with blend factors 1.10/1.50 receives factor 1.34.
A modelled ROM grade of 50 therefore becomes 67. With regression factors
0.90/1.30, the same source receives factor 1.14; a modelled product grade of
60 becomes 68.4. Blend is not also multiplied into the wet-plant product grade.

The factor weights use ROM WMT, as confirmed in Q39. They do not use product
tonnes or grade values. Original mapped insitu and wet-plant modelled grades
remain the baselines; Task 7 does not reconstruct them from component grades.
Repeated enrichment starts from those baselines, so adjustments do not compound.

Existing plant conventions remain:

- Dry-plant product streams follow adjusted ROM.
- AMT at an unconfirmed product channel retains an unavailable product vector,
  allowing the existing per-analyte fallback to ROM.
- The corresponding existing inventory product-alias behavior is preserved.
- APS grade blocks do not enter the new application API.
- Physical balances, product masses and recovery values are not changed.

## Lineage and missing data

AMT uses `grade_block_name` and aligned `remaining_wmt` from
`GRADE_BLOCK_LINEAGE_JSON`. Original inbound tonnes are not reused as current
component weights. An EXPIT ID without a valid spatial grade-block name remains
unknown lineage.

Conventional inventory uses Task 5's lineage for the **exact opening build**.
If the selected inventory balance differs from the fetched reference balance,
composition scales proportionally to the current physical balance. Another
build of the same footprint cannot supply its lineage.

Missing source fractions retain the supplied standard global factors and their
physical weight. Malformed source lineage also produces an explicit global
fallback and warning. Missing history uses the standard factors supplied by the
existing service. The application does not create replacement unit factors.

Missing mapped assays remain missing; global factor availability does not fill
them. Adjusted-grade coverage inherits the mapped baseline's available coverage.
Coverage from the AMT raw property catalogue follows the selected Map Fields
source. Clearing a mapping clears previous baseline and adjusted values, so a
refresh cannot revive a stale grade.

Zero source tonnes produce no adjustment contribution or confidence score and
are never inflated by this layer. Task 9's separate footprint tonnage guard is
outside Task 7.

## Chunk aggregation, confidence and refresh

Chunks average **already adjusted hex grades** using each field's declared
Define Fields weight. ROM WMT and product DMT can therefore produce different
weights. Each grade uses only member hexes with a valid grade and positive
declared weight. Missing assay or weight coverage remains explicit.

Canonical property reweighting preserves these advanced adjusted grades. Saved
chunk refresh also preserves current advanced results instead of applying a
global factor to the chunk average. A compact enrichment fingerprint identifies
the history, factor, mapping and scenario inputs that produced the member hexes.
If a stale chunk cannot be rebuilt from current members, it receives an explicit
global fallback rather than silently retaining an old advanced adjustment.

Cloudbreak's calculated lump/fines split uses the advanced adjusted SF head
grade. Its separate CBFL-campaign fines-regression factor continues to follow
the existing campaign-specific calculation. Lump grades are still back-calculated
to preserve the adjusted head grade when the two product sizes recombine.

Confidence continues to use Task 6's spatial-and-compositional diagnostic score,
not a statistical probability or confidence interval. Chunk confidence and
lineage/global fractions use physical ROM WMT, independently of product-grade
weight choices. Chunk grade coverage additionally requires a valid grade and
declared weight. Unknown or unaudited member mass remains in the confidence
denominator.

`overall_reconciliation_confidence()` combines selected conventional inventories
and submitted AMT chunks once each. Before a footprint has submitted chunks,
its current hexes supply the summary. The AMT inventory header is not counted
again alongside its hexes/chunks. Summaries remain scoped to one OPF.

## Interfaces and storage

The pure application is `classes/ReconciliationApplication.py`:

```python
from classes.ReconciliationApplication import ReconciliationApplication

application = ReconciliationApplication(
    samples=samples,
    standard_factors=global_factors,  # whole brand registry, unlike Task 6
    opf="CB OPF", brands=["SF"], scenario_start="2026-08-22 06:00:00",
    settings={
        "method": "spatial_compositional",
        "max_lookback_days": 30,
        "min_production_days": 3,
    },
)
adjusted_streams, audit = application.apply(
    existing_streams,  # produced from current mapped baselines
    source_id="SP1", source_kind="amt", hex_id="H1",
    source_wmt=100,
    contributing_blocks=[
        {"grade_block_key": "PIT01|1|453|426|456|LG01", "feed_wmt": 100},
    ],
)
```

Use `source_kind="inventory"` without `hex_id` for a conventional stockpile.
Optional `grade_coverage` supplies per-analyte baseline coverage. The standard
application method leaves streams produced by the existing standard path intact;
the GUI rebuilds that standard path when switching back from advanced mode.

The existing GUI orchestration now accepts these backend state fields:

- `reconciliation_settings`: method and Task 6 lookback/production-day controls;
  absent settings mean Standard.
- `reconciliation_inputs`: `samples`, exact-build `inventory_lineage` and
  acquisition `warnings`.

The Data Streams background worker fetches advanced history and selected
conventional inventory lineage in bulk when advanced mode is configured. Standard
mode issues no advanced queries. Resolver instances are reused across sources;
changing effective factors, settings or inputs invalidates the application cache.
Settings and inputs are retained in site/scenario and project snapshots.

Source rows carry a `reconciliation` audit with per-brand component records,
applied factors, global/spatial fractions, grade coverage and confidence. Full
component evidence stays on the source/hex; chunks contain compact member
references and weighted summaries. AMT's existing SQLite snapshot gains an
optional `reconciliation_json` column so its map/chunk reload retains the audit.
The inventory snapshot writer can retain the same audit. Broader persistence and
cache migration work remains Task 34.

## Validation completed

From `C:\BlendMaster\blendmaster_OOP`:

```powershell
python -m unittest tests.test_reconciliation_application -v
python -m unittest discover -s tests
```

- 35 new tests cover factor application, plant conventions, unknown lineage,
  null grades, coverage, idempotence, changed mappings, multiple brands, source
  isolation, chunk weights, refresh, calculated CB split, state snapshots and
  SQLite audit round trips.
- Full suite: **577 tests passed**. The two pre-existing pandas warnings remain.
- Read-only live validation used 120 CBSF samples over 60 shifts ending
  22 August 2026 at 06:00 Perth, with a three-production-day minimum.
- Two conventional inventory builds retained their imported baselines and
  physical balances while receiving advanced blend/regression factors:

| Build | Opening WMT | Modelled ROM Fe | Adjusted ROM Fe | Modelled product Fe | Adjusted product Fe |
| --- | ---: | ---: | ---: | ---: | ---: |
| KAN82_RP01_0003_26001 | 5,124.156 | 54.068643 | 53.325255 | 55.355541 | 55.999646 |
| BIG01_RP01_0007_26005 | 124,780.276 | 52.884979 | 52.166351 | 54.376661 | 55.109317 |

- Live KAN82 AMT returned 226 hexes, of which 44 had positive balances. All
  **440** non-null adjusted-grade calculations matched independent calculations
  from their component factors to relative tolerance 1e-12.
- The positive hexes contained 252 component resolutions at the most specific
  level and one explicit global component. Local enrichment of all 226 hexes
  took approximately **1.75 seconds**, excluding warehouse reads.
- A validation chunk spanning the 44 positive hexes retained 5,124.156 WMT.
  Its adjusted ROM Fe was 52.877388 and product-DMT-weighted adjusted product Fe
  was 55.376984. Both matched independent member-weight calculations and remained
  unchanged after the saved-chunk refresh. Its diagnostic confidence was 12.4623%.
- The initial two-build AMT query hit a 60-second validation timeout. A single-build
  retry completed in 75.41 seconds under a 120-second validation limit. The
  application query and its default timeout were not changed by Task 7.
- Live validation wrote no warehouse or application database records. Persistence
  tests used temporary SQLite databases. The live extract was reused locally for
  application and chunk checks without further warehouse reads.

Task 7 is complete. Task 8 has not started.
