# Task 12 — Product quality-limit data and inputs

Implemented 7 September 2026.

Product Targets now has optional **LQL**, **Target** and **HQL** values for each
of Fe, Si, Al, P and Mn on every build row. The row owns the values together with
its OPF, brand and product lane. Lump and fines can have different specifications
even when their displayed brand is the same. There is no separate brand lookup
table and editing one row does not change other rows of that brand.

## Using the inputs

In **Product Targets → Grade fields**, choose:

- **Min / Max**: the existing active solver bounds; this remains the default view.
- **LQL / Target / HQL**: the new reference specifications on the same rows.
- **All grade fields**: both sets together.

The selector changes visible columns only. A permanent note explains that
Min/Max remain active and the quality specifications do not currently constrain
the solver. The OPF column displays the row's owner; new manual rows use the
active OPF and imported rows retain their imported OPF. Brand and by-product
remain editable. Submit stores the complete row, including hidden grade fields.

For example, a fines row can have Fe LQL 57, Target 58.123 and HQL 60, while a
lump row of the same brand has Target 61.5 with blank quality limits. Their
Min/Max values continue to determine current feasibility.

## Values and validation

- Canonical row fields are `target_<analyte>_lql`,
  `target_<analyte>_target`, and `target_<analyte>_hql`.
- Blank/missing values mean **unspecified**, stored as `None`. Zero is a valid
  explicit value and is preserved, including an explicit zero HQL.
- Supplied values must be finite numbers from 0 through 100 percent. Invalid
  text, booleans, infinity, NaN and values outside that range are rejected.
- When both limits are present, LQL must not exceed HQL. A supplied Target must
  be at least any supplied LQL and at most any supplied HQL. Partial/open
  specifications are allowed; a Target is not required merely to store limits.
- Quality specifications are validated independently of current Min/Max. They
  do not replace, tighten or infer values from those active bounds.
- Table submission and row resizing stop on invalid specifications without
  replacing saved rows. Agent application validates quality values before
  replacing current targets or advancing its workflow. Runtime normalization
  validates again for non-UI callers.

`classes/ProductQualityLimits.py` is the shared boundary for extraction,
validation and runtime configuration. It derives the existing version-1
`PhaseSchemas.product_quality_limits` record, with OPF/brand/lane and all five
analytes, explicitly marked `enforcement: reference_only`. The flat row fields
are authoritative; the runtime record is reconstructed from them.

## 2WP import and grouping

New 2WP imports seed each Target from its planned grade and retain the original
values in `planning_grade_targets`. LQL and HQL remain blank for manual entry.
Missing planned grades remain unspecified rather than being converted to a
zero Target. A genuine planned zero remains zero.

Existing Fe minimum / contaminant maximum imports and open opposite bounds
keep their hard-mode meaning. Legacy rows without an explicit Target do not
infer one from a potentially edited hard bound.

Consecutive compatible builds group with a tonne-weighted Target, preserving
full calculation precision. OPF, product lane, intervening brand changes and
different manual LQL/HQL specifications prevent inappropriate merging. Limits
are retained as specifications, not averaged. A grouped Target remains blank
if a positive-tonnage member lacks that Target; missing evidence is not silently
treated as zero or removed from the denominator. Planning-grade provenance is
aggregated separately so manually edited targets are not relabelled as 2WP.

This task preserves the new quality values through table editing without
rounding. [Task 13](TASK_13_TARGET_PRECISION.md), subsequently completed, adds
three-decimal display with full stored precision for imported 2WP grades,
including existing Min/Max cells.

## Persistence, agent workflows and row ownership

Projects, inactive scenarios, Calendar inputs and outgoing agent context retain
the new flat fields under canonical `product_targets`. Existing legacy aliases
continue to work. Agent `grades`, `grade_targets` and `target_grades` dictionaries
can supply `{lql, target, hql}` per analyte; explicit canonical fields, including
null, take precedence.

OPF, crusher, by-product, campaign, contribution ratio and planning metadata
stay attached to the actual table row when rows are deleted or recreated. This
also corrects the previous index-based planning-metadata recovery, which could
attach the deleted first row's provenance to the next surviving row.

## Solver and reporting handoff

- CaseModeller and ManualBlendPlanner retain validated quality values and the
  derived reference record. Current-step solver configuration carries them in
  `target_product_builds` per lane and the established primary-build alias.
  Nested records are detached from saved build settings.
- Optimizer constraints, objectives, repairs and existing on-spec status continue
  to use Min/Max. Task 16 adds hard/soft mode configuration; Task 17 implements
  the soft optimization and optional quality-limit enforcement. This task does
  not label a reference-limit breach as an active-constraint breach.
- Product-build reports include `opf` and all fifteen canonical quality fields.
- Source-level optimized/manual progress reports include
  `product_build_target_<analyte>_{lql,target,hql}`, plus the corresponding
  `product_build_lump_...` and `product_build_fines_...` fields and OPF ownership.
  Existing DataFrame/CSV/SQLite report paths retain these fields and nulls.
- No production-assay overlays, breach magnitudes, penalty diagnostics or new
  report tab are introduced here; those remain in their scheduled report tasks.
- Progress audit columns are added as a batch to avoid DataFrame fragmentation
  as the report schema grows.

## Validation

`python -m unittest discover -s tests -q`: **747 tests passed**, including **22
new tests** for optional/zero values, invalid inputs and ordering, ownership,
2WP targets and grouping, legacy and multi-scenario persistence, table edits and
deletion, agent application, runtime configuration, manual planning, report
propagation and temporary SQLite round trips. The Task 0 characterization test
marked for replacement in Task 12 now checks that legacy quality fields are open.

A real CBC check proves that an otherwise feasible source can breach reference
LQL/HQL while remaining selectable, and that tightening its active Fe Min makes
it infeasible. Existing hard-bound compatibility tests, including the legacy
falsy upper-bound coercion, pass.

Both native Qt views were rendered with installed Segoe UI fonts and visually
checked using separate lump/fines specifications. Compilation and
`git diff --check` pass. The two pre-existing pandas warnings remain in
CaseModeller and DrawCharts. Validation used mocked 2WP history and temporary
databases; no warehouse or production database changes were made.

Restart BlendMaster to load the new inputs. Task 13 is also complete;
**Task 14 — Build a reusable product-assay history service** is next.
