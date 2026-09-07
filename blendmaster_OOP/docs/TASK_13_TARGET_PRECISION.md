# Task 13 — Three-decimal 2WP target precision

Implemented 7 September 2026, following clarification Q51: the display change
applies to product targets imported from 2WP. Calculation values retain their
full floating-point precision.

## Visible behavior

On a 2WP-imported row, Fe, Si, Al, P and Mn **Min**, **Max** and central **Target**
cells display exactly three decimal places. This includes grouped imports and
imported rows restored from projects or agent payloads. Blank central Targets
remain blank; a genuine zero displays `0.000`.

For example, imported Fe `58.123456789` displays `58.123` and P `0.085123456789`
displays `0.085`. Editing either cell reveals its full value. Copying a cell also
copies the full value, so copying and pasting within the table does not silently
round a target. A tooltip explains the display and stored-value distinction.

Editing a value on an imported row retains that row's three-decimal presentation.
An explicit replacement is authoritative: typing `0.085` replaces the prior
`0.085123456789`; merely opening and committing the editor preserves the original.
Escape cancels an edit. Pasted values replace the underlying values directly.

Manual rows retain their existing formatting, including two-decimal Min/Max.
Manually entered LQL/HQL, target-tonnage formatting, other grade tables and report
display formats are unchanged. The existing Min/Max constraints remain active;
Task 12 quality specifications remain reference values.

## Precision through the application

The defect was in Product Targets table population: it formatted imported hard
bounds to two decimals and later parsed those shortened strings on submission,
row recreation, project capture or agent application.

`GUI/ProductTargetDelegate.py` now formats only the painted cell text. Imported
items retain their unrounded editable text as the single stored value. The normal
Qt editor, clipboard, validation and persistence paths therefore read the same
full value, without a separate cache that could become stale after editing.
Planning-scenario metadata also identifies older 2WP rows that predate
`planning_grade_targets`.

The existing downstream calculations needed no rounding changes:

- 2WP numeric ingestion retains its floating-point value; consecutive compatible
  builds use unrounded tonne-weighted averages.
- Table submission, resizing, deletion and view changes retain imported values.
  Validation compares full values even when rounded cell displays look equal.
- Project/scenario serialization, Calendar state and agent normalization/application
  carry numeric values, with planning provenance retained on each row.
- CaseModeller passes full bounds and central Targets into current-step solver
  configuration. CBC constraints and structured diagnostic bounds retain them.
  ManualBlendPlanner and source-progress reports retain them as well.
- Product-build report DataFrames, SQLite numeric columns and CSV exports retain
  the full numbers. Existing textual diagnostic and report formatting remains
  unchanged under Q51.

This preserves the precision available on import; it does not infer digits that
were already lost in older saved Min/Max values. Reload 2WP targets to restore the
original planned values in those projects. Existing edited bounds are preserved.

## Validation

`python -m unittest discover -s tests -q`: **760 tests passed**, including **13
new precision tests** in `tests/test_product_target_precision.py`. The focused
target/import suite passed all **131 tests**.

Coverage includes decimal query inputs, weighted grouping, three-decimal native
Qt presentation, unchanged manual formatting, legacy import provenance, real Qt
editor commits/cancellation, copy and multi-cell paste, blanks/zero/invalid input,
full-precision validation, row recreation/deletion, project/scenario and agent
round trips, manual planning, report propagation and temporary SQLite/CSV exports.

Two real CBC checks demonstrate that the precision matters:

- Fe `58.1232` fails an imported minimum of `58.123456789`, although it would pass
  if the displayed `58.123` were used instead.
- P `0.0851` passes an imported maximum of `0.085123456789`, although it would fail
  if the displayed `0.085` were used instead.

Both Min/Max and LQL/Target/HQL views were rendered with native Qt and installed
Segoe UI fonts, using imported and manual rows, and visually checked. Compilation
and `git diff --check` pass. The two existing pandas warnings in CaseModeller and
DrawCharts remain. Validation used mocked warehouse inputs and temporary reports.

Restart BlendMaster to load the display change. **Task 14 — Build a reusable
product-assay history service** is next.
