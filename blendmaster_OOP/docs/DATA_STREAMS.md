# Data Streams

BlendMaster retains five grade families for Fe, SiO2, Al2O3, P and Mn and
projects one selected stream onto the existing optimiser grade fields:

1. `insitu_<grades>`
2. `modelled_rom_<grades>`
3. `adjusted_rom_<grades>`
4. `modelled_product_<grades>`
5. `adjusted_product_<grades>` (default)

Resolution falls back independently per analyte in the order selected stream,
next available upstream stream, then the legacy grade. A warning is retained
when fallback occurs.

## Independent optimiser tonne streams

The **Optimiser Grade Stream** selection answers only *which grades* are constrained. The
Data Streams page separately selects the additive field used for each mass
basis:

- **Crusher Quantity Field** (default `modelled_rom_wmt`) drives only crusher
  throughput and capacity. It does not change grade weighting.
- **Reclaimer Quantity Field** (default `modelled_rom_wmt`) drives the reported
  reclaimer rate and per-source reclaim capacity. Physical source depletion
  and opening/closing balances remain ROM WMT so inventory reconciliation is
  never distorted.
- **Product Build Quantity Field** (default `modelled_product_wmt`) drives
  product-build accumulation and completion. It does not override a grade's
  configured weight field.

Calendar grades, product-build grades, optimised profiles and manual profiles
all use the selected Optimiser Grade Stream family. Each analyte is independently
weighted by the additive Weight Field configured for that grade in Define
Fields. For example, an adjusted product Fe field weighted by
`modelled_product_dmt` continues to use product DMT even when crusher capacity
is configured in ROM WMT.

For example, selecting `adjusted_product` as the optimiser grade stream does
not turn a 100 kt ROM source into 100 kt of product. Its product build receives
only that source's mapped `modelled_product_wmt` (or the user-selected product
tonne field). The selected fields are loaded even when they are not checked as
general optimisation properties, because they are required to define the
solver's physical bases. Legacy rows without the canonical field fall back to
their physical ROM quantity to remain runnable.

For every additive field enabled for optimisation, the source-level reports
carry its opening balance, transaction depletion (the unsuffixed
`source_property_<field>` column), and closing balance. Stockpile build
transactions additionally carry `<field>_opening_balance`, `<field>_built`
and `<field>_closing_balance`.

## Canonical field workflow

The Setup sequence is:

```text
Site Configuration -> Guidance Schedules -> Stockpile Inventories
-> Define Fields -> Map Fields -> Data Streams -> AMT Stockpiles (when selected)
-> Database View
```

**Guidance Schedules** precedes inventory selection so the 24HR APS schedule
can supply each stockpile's 2WP brand and the haul-cycle selection can supply
its nearest crusher. **Stockpile Inventories** then preselects both **Use** and
**AMT** only for stockpiles whose nearest crusher matches one of the explicitly
selected planned tipping points and which have 2WP brand guidance. It remains
intentionally pre-calculation: it shows source selection,
inventory identity/build, opening balance and insitu assays. The modelled and
adjusted fields are not treated as defined until the next steps. The submitted
opening inventory is persisted here; Data Streams does not rewrite it.

Guidance Schedules also accepts an optional **2WP Closing ROM Stocks.xlsx**
workbook with exactly these four columns:

```text
Source.Name
Period.Start Datetime
Period.End Datetime
Mining.wetTonnes
```

`Mining.wetTonnes` is the planned closing ROM WMT at the explicit period end.
The normalized rows are stored in the project, so an already-prepared project
does not depend on rereading the workbook simply to run its reports.

### Current-time ExPit sequence reconciliation

When **Update Transactions on Current Time** is selected, BlendMaster no
longer assumes that the load agent followed APS merely because a cumulative
tonnage threshold was reached. It queries
`AA_OPERATIONS_MANAGEMENT.SELFSERVICE.INVENTORY_EXPIT_REHANDLE_TRANSACTIONS`
for `MOVEMENT_TYPE = 'ExPit'`, matching the selected load agent and resolving
the parent grade block from `SOURCE_FMS` (or `SOURCE` when required).

Actual WMT between the earliest selected APS 24HR `Time.StartTime` and the
scenario start is deducted from the future APS sequence. The immediately
preceding 24 hours is retained only as route/direction context and is never
deducted from the current plan. Snowflake actuals identify parent blocks,
whereas APS may contain operational slices. BlendMaster therefore determines
completion and remaining WMT at parent level, but preserves the APS slice and
payload rows internally so their timing, grades, destinations and haulage
properties remain authoritative for the solver.

The parent audit also queries the latest active matching record in
`DA_OPERATIONS.STG_GRADECONTROL.GRADE_BLOCKS`, using only the parent identity,
`GB_WET_TONNES`, `GB_DRY_TONNES`, `GB_MATERIAL`, `IS_ORE` and record timestamp.
These geological values are context only: they never create payloads or replace
the APS schedule. The displayed measures distinguish:

- `aps_planned_wmt`: APS `Mining.wetTonnes` summed across slices of the parent;
- `actual_schedule_wmt`: ExPit WMT inside the schedule-to-scenario-start window;
- `aps_remaining_wmt`: scheduled tonnes still retained as future payloads;
- `nominal_geological_wmt` / `nominal_geological_dmt`: original active grade-
  block model tonnes;
- `cumulative_actual_wmt`: all available non-deleted ExPit WMT for the parent
  across the full transaction table, with no time predicate;
- `estimated_geological_remaining_wmt`: nominal WMT less cumulative actual WMT,
  clamped at zero;
- `aps_share_of_nominal_pct`: scheduled APS WMT as a share of nominal WMT; and
- `geological_depletion_pct`: cumulative actual WMT as a share of nominal WMT.

Schedule completion alone controls APS payload removal. Geological completion
is an audit/map signal and may exceed 100% where surveyed/transaction precision
or model revisions differ; that condition does not change scheduled payloads.
For active blocks, the displayed polygon area represents the estimated remaining
geological fraction and is clipped progressively from north to south. Completed
blocks remain available as a separately toggleable, muted footprint layer.

The **Grade Block Completion Tolerance** defaults to 10%. A parent is complete
when actual WMT is at least 90% of planned WMT; an overrun above 110% is also
complete. A partial parent retains `max(planned WMT - actual WMT, 0)`, consumed
from its original slices and payloads in time order. Actual blocks absent from
the APS sequence are retained as historical route evidence only: BlendMaster
does not manufacture future payloads, grades, destinations or tonnes for them.

Waste rows in the APS route contribute to direction, reversals, future timing
and the reconstructed face, but are removed before the payload population is
sent to the ore/direct-tip optimiser. If no usable actual rows are returned
for one agent, that agent's original APS payload sequence is retained with an
explicit fallback warning; it is not silently dropped.

The **Workspace > Expit Sequence** tab shows the original APS parent route,
the complete chronological actual route (including returns to earlier blocks),
the corrected future route, polygon geometry from
`DA_OPERATIONS.STG_GRADECONTROL.GRADE_BLOCK_POLYGON_POINTS`, the latest agent
block, completion metrics, inferred direction/reversals and confidence. It has
**Refresh Now** plus opt-in live refresh, disabled by default with a five-minute
default interval. Ore, waste, actual-only and completed polygons and each of the
original, corrected and actual routes can be toggled independently. Completed
blocks are visible by default as muted dotted audit footprints and can be hidden.
The latest agent block uses the transparent PNG excavator marker, sized below the
average equivalent block width. The corresponding SQLite audit tables are:

- `expit_sequence_reconciliation_audit`;
- `expit_sequence_reconciliation_summary`;
- `expit_sequence_actual_movements`; and
- `expit_sequence_geometry`; and
- `expit_sequence_geological_blocks`.

After an optimised, contingency or manual plan is produced, **Closing ROM
Stocks Compliance** appears directly after **Build and Depletion Profiles** in
Results. For every BlendMaster period boundary it compares physical remaining
ROM WMT with the latest completed 2WP period whose end is at or before that
boundary. Inventory sources use their remaining inventory balance; AMT sources
use the remaining reconciled AMT chunks without a second inventory adjustment.
The comparison is limited to stockpiles used as a source or destination in the
2WP or BlendMaster plan and reports both WMT and percentage variance.

**Define Fields** creates the stable BlendMaster schema shared by Inventory,
AMT and APS sources. A field name may contain letters, numbers and underscores
and must start with a letter. Each row is one of:

- **Additive**: a total such as WMT, DMT, ore-type tonnes or product tonnes.
  Additive values are summed when sources are consolidated and scaled when a
  source is partly depleted or selected.
- **Weighted Average**: a grade, percentage, recovery, moisture or other
  intensive value. Its **Weight Field** must be an additive field defined in a
  higher row. This makes the mass basis explicit rather than inferring it from
  a Snowflake or APS header name. Consolidated inventory, AMT chunk and APS
  values use that additive field as their actual denominator, including the
  grade stream subsequently selected by optimisation.

Required rows cannot be deleted. The default contract includes
`insitu_<analyte>`, `modelled_rom_<analyte>`, `adjusted_rom_<analyte>`,
`modelled_product_<analyte>` and `adjusted_product_<analyte>` for the five
analytes, `modelled_rom_wmt`, `modelled_rom_dmt`, `modelled_product_wmt`
and `modelled_product_dmt`.
Insitu/modelled/adjusted ROM grades use `modelled_rom_wmt`; modelled/adjusted product
grades use `modelled_product_dmt`. Map the product fields to the active OPF
product channel's per-source Product 1/2/3 WMT/DMT. The internal balance
tracker derives its physical source WMT from `modelled_rom_wmt`; there is no
second user-defined or separately mapped `source_wmt` field. Required fields
may be left unmapped; they then remain blank and traceable rather than being
silently removed from the source schema.

**Use in Optimisation** is the compute/report gate. Checked properties are
available in the custom-constraint field picker, carried through build,
depletion and event generation, and written to optimised/manual reports.
The additive Weight Field behind a required or checked weighted-average field
is checked automatically because it is part of the same calculation. Other
unchecked properties stop at Database View. The five effective selected grades
are always sent to optimisation independently of this checkbox.

The checkbox does not manufacture a value or mapping. For example,
`modelled_rom_dmt` must be mapped to the applicable Inventory/AMT **Insitu /
ROM DMT** source field (raw compatibility ID `feed_dmt`). If it is checked but
unmapped, its report columns are deliberately
present and blank; a custom constraint that requires it cannot obtain a valid
coefficient. This makes the data gap distinguishable from a genuine zero.

**Map Fields** maps raw Inventory, AMT and APS columns onto the canonical rows.
Choose a source family (and, for APS, an optional brand), then double-click or
drag an available raw field into **Source Field**. Guidance Schedules owns the
initial 24HR `Mining.csv` selection and Map Fields exposes the same shared path
so it can still be replaced while mapping. Mappings save
the exact raw header while every downstream component uses only the stable
BlendMaster field name. Older projects migrate their former APS mappings and
receive explicit compatibility mappings for the established inventory and AMT
stream inputs.

An empty mapping is strict: a raw field with the same spelling cannot flow to
Database View, the balance tracker, custom constraints, or the solver as that
BlendMaster field. The exceptions are explicit calculated/system outputs:
`source_wmt` is the internal physical balance; adjusted grades are calculated
from mapped modelled grades and historical factors; APS payload WMT is supplied
by the schedule transaction. `modelled_rom_wmt` may be synchronized to that
physical balance after it has been mapped; missing product/custom quantity
fields never fall back to physical ROM tonnes.

Database View may still display fallback provenance so a setup gap is visible
before running. In the current strict setup contract, however, optimisation and
manual planning require the exact selected Optimiser Grade Stream for every
analyte. An unbranded value in that same stream may serve any brand; falling to
a lower stream or legacy grade is rejected with the source and analytes named.

The Available Source Fields list is intentionally limited to grade fields and
additive quantity fields (WMT, DMT or tonnes). Coordinates, timestamps,
reconciliation metadata and other operational columns are not mappable.
Lineage and coverage metrics also stay out of Map Fields: BlendMaster carries
them automatically as audit metadata and exposes them through Database View's
coverage-field option.
Cloudbreak lump/fines fields use only the Product 1-qualified form in this list
(for example, `modelled_prod1_fines_fe`). Short compatibility aliases such as
`modelled_fines_fe` remain readable in older saved mappings but are hidden when
the qualified field is available.

For AMT hexes, the browser keeps the native AMT assays (`FE`, `SIO2`, `AL2O3`,
`P`, `MN`) and one explicit flattened `MODELLED_*` field for every available
grade-block-lineage assay. The equivalent nested aliases (`grade_block_fe`,
`prod1_fe`, `feed_loi_total`, and corresponding fields for every other assay)
are hidden. Canonical downstream fields such as `insitu_fe`,
`modelled_rom_fe` and `modelled_product_fe` are also hidden because they are
mapping/calculation outputs, not source inputs. Existing saved mappings to a
hidden alias remain valid.

The flattened names in that browser are virtual aliases over one compact
per-hex `modelled_properties` JSON catalogue; BlendMaster no longer stores a
second physical column for every `MODELLED_*` value and coverage percentage.
The AMT SQLite table contains the fixed balance/geometry/lineage audit columns,
the compact raw catalogue, grade-stream JSON and the canonical fields created
in Define Fields. This preserves every raw option for remapping while avoiding
hundreds of duplicate columns in memory, project files and the opening table.

The AMT source browser always exposes the supported grade-block-lineage
additive schema: Product 1/2/3 WMT and DMT, Product 1 lump/fines WMT and DMT,
Product 1/2 minus-1-mm WMT and DMT, and ore-type WMT and DMT. These are per-hex
modelled masses. They remain visible but blank when the selected lineage has no
valid value, making the gap traceable in Map Fields and Database View instead
of making the field itself disappear.

Spatial-reconciliation ledger quantities such as `RAW_WMT`,
`SPATIALLY_CORRECTED_WMT`, `LEDGER_ADJUSTMENT_WMT` and
`FINAL_STOCKPILE_WMT` are audit metadata and are intentionally hidden from Map
Fields. `FINAL_STOCKPILE_WMT` is the whole-footprint total repeated on every
hex; it must never be mapped as an additive hex property. The authoritative
per-hex insitu/ROM quantity is `feed_wmt` (displayed as **Insitu / ROM WMT**),
which is sourced from the reconciled per-hex `FINAL_WMT`.

AMT mappings apply independently to each imported **hex** before chunks are
formed. The AMT source list includes lineage-derived per-hex fields, including
`prod1_wmt`, `prod1_dmt`, `prod2_wmt` and `prod2_dmt` where the contributing
grade blocks supply explicit modelled product tonnes, including Product 3.
It also includes **Insitu / ROM WMT/DMT** (stored raw as
`feed_wmt`/`feed_dmt` for compatibility); map these to
`modelled_rom_wmt`/`modelled_rom_dmt`. Map the active product channel to
`modelled_product_wmt` and `modelled_product_dmt`; the product-grade fields
then use that DMT denominator when hexes are consolidated into chunks. The
same canonical mass mappings are available for inventory stockpiles, whose
`feed_wmt` is their opening balance and whose `feed_dmt` is its dry equivalent.

Chunks are a canonical scheduling layer, not another copy of the raw AMT
catalogue. Chunk construction aggregates only Define Fields values, selected
grade streams and the compact lineage/product-coverage audit needed for
warnings. Unmapped raw candidates remain on the underlying hex snapshot and
can still be mapped later; changing a mapping causes the affected chunks to be
rebuilt from their member hexes.

The required `insitu_*` fields are visible in Map Fields for Inventory, AMT
and APS. They default to the established raw assay fields during migration but
remain explicit and user-editable like other source mappings.

`adjusted_rom_*` and `adjusted_product_*` are deliberately absent from Map
Fields—both from the canonical mapping targets and the Available Source Fields
browser. They are calculated fields: adjusted ROM is modelled ROM multiplied by
historical blend recon, and adjusted product is modelled product multiplied by
historical regression recon (or adjusted ROM for dry plants).
Compatibility seeding runs once. Clearing a suggested mapping is therefore a
persisted user decision; it is not recreated when mappings are applied or the
project is loaded again.

## Source calculations

APS grade blocks use the exact brand-specific ROM and product headers mapped on
the Map Fields page. Legacy projects with one all-brand ROM mapping replicate
that mapping across configured brands. APS grades are authoritative, so no OPF
factor is applied.

Inventory stockpiles use:

- Modelled ROM = imported inventory insitu grade
- Adjusted ROM = Modelled ROM (inventory insitu) x historical blend recon
- Modelled Product = imported inventory product for the OPF product channel
- Adjusted Product = inventory product x historical regression recon

The imported inventory ROM fields remain available for auditing, but they do
not drive the inventory Modelled ROM or Adjusted ROM streams.

Inventory-only scenarios use the same downstream data-stream path as AMT
scenarios. Each selected inventory stockpile carries its complete
`grade_streams` and numeric source properties into its solver event; the active
Calendar brand then resolves the configured stream independently for Fe, SiO2,
Al2O3, P and Mn using the same fallback rules. The resulting selected grades,
available balance and source properties are the values shown in Database View,
passed to the optimiser and retained in reports. AMT selection only replaces a
selected footprint with its spatial chunks; it does not enable a separate
product-grade calculation path. Consequently an inventory-only run keeps its
inventory rows, product streams and adjusted product grades without requiring
an AMT map or hex sequence.

The opening inventory snapshot also retains the extended APS stockpile
properties available at the scenario start time. These include extended
insitu/ROM/product/OPF/train chemistry, moisture and wet/dry yields, WHIMS
minus/plus 1 mm, ore-type proportions and DMT, modelled Product 2 WMT/DMT,
grade-block-derived Product 1 minus 1 mm, fines/lump yields, moisture,
chemistry, WMT, DMT and volume, dry density, material classification, and the
source stockpile metadata. The grade-block-derived fields use movements into
the latest stockpile build as of the scenario start time. They are audit/model
input properties; the existing five-analyte grade-stream selection remains the
grade vector used by the optimiser.

Source properties use the same canonical names for inventory, AMT and APS
sources. Important additive families are:

- insitu/ROM WMT and DMT (stored raw as `feed_wmt` and `feed_dmt`);
- `oretype_<type>_wmt` and `oretype_<type>_dmt`, where `<type>` is `bid`,
  `cidl`, `cidm`, `cidu`, `did`, `hc` or `other`;
- `prod1_wmt` through `prod3_wmt` and their `_dmt` equivalents;
- `prod1_minus_1mm_wmt` through `prod3_minus_1mm_wmt` and their `_dmt`
  equivalents; and
- CB `prod1_fines_wmt`, `prod1_lump_wmt` and their `_dmt` equivalents.

Percentages, recoveries, moisture and component assays are intensive
properties rather than additive tonnes. For example,
`prod1_minus_1mm_pct`, `prod1_fines_yield_pct` and `prod1_lump_fe` are
intensive values. This distinction controls how properties are combined,
depleted and offered to custom expressions.

For inventory stockpiles, the opening query calculates these additive values
directly from the latest positive build balance at scenario start:

```text
Insitu/ROM WMT (`feed_wmt`) = BALANCEWMT
Insitu/ROM DMT (`feed_dmt`) = BALANCEWMT x (1 - insitu moisture)
oretype_<type>_wmt = BALANCEWMT x insitu ore-type fraction
oretype_<type>_dmt = feed_dmt x insitu ore-type fraction
prod<n>_wmt = BALANCEWMT x Product n wet yield
prod<n>_dmt = feed_dmt x Product n dry yield
```

Inventory Product 1 minus-1-mm and CB lump/fines properties are obtained by
linking EXPIT movements into that build to
`DA_OPERATIONS.STG_GRADECONTROL.GRADE_BLOCKS`. Minus-1-mm is WMT-weighted;
fines/lump yields and assays are DMT-weighted; size moisture is WMT-weighted.
The resulting percentages are converted to `prod1_minus_1mm_wmt` and `_dmt`,
and the size yields to `prod1_fines_wmt`, `prod1_lump_wmt` and their DMT
counterparts. Inventory values therefore enter Database View and constraints
without requiring AMT mode.

For CC OPF02, the inventory table's applicable product is physically stored
in `PROD3`, while EXPIT and AMT expose that same logical channel as `PROD2`.
BlendMaster retains the raw PROD3 fields for audit and projects them onto the
canonical `prod2_*` names before Database View, constraints and reports. When
the inventory build supplies only the generic grade-block minus-1-mm value,
the canonical `prod2_minus_1mm_wmt` and `_dmt` totals are calculated from the
canonical Product 2 mass. This keeps inventory-only, AMT and mixed runs on the
same field names.

AMT hexagons use:

- Modelled ROM = hex insitu baseline; no inventory-derived internal blend factor
  is applied
- Adjusted ROM = hex insitu x historical blend recon
- Modelled Product = grade-block-lineage-weighted EXPIT product grades for the
  applicable OPF product channel
- Adjusted Product = Modelled Product x historical regression recon

The former AMT workaround based on inventory `ROM / insitu` and `PRODn / ROM`
factors is superseded by grade-block attribution. CB and CC OPF01 use EXPIT
Product1; CC OPF02 and VK/KV use EXPIT Product2. EW and FT are dry plants, so
product streams alias Adjusted ROM and regression is locked to 1.0. IB product
mapping remains unconfirmed and therefore falls back to Adjusted ROM with a
warning.

AMT insitu grades continue to come from the AMT hex grade table. Grade-block
lineage supplies modelled product chemistry and modelled physical properties
such as ultrafines, recovery and ore type. Physical properties remain modelled
only; blend and regression factors apply only to the five grade analytes. The
complete opening-tonnage and lineage method is documented in
[AMT Opening Hexes and Grade-Block Lineage](AMT_OPENING_HEXES_AND_GRADE_BLOCK_LINEAGE.md).

### Cloudbreak Product 1 lump/fines

The Cloudbreak-only **CB Lump/Fines Source** option controls inventory and AMT
stockpile properties. It does not replace the mapped APS grade-block values,
which APS has already calculated.

- **Derive from grade blocks / mapped APS fields** retains the opening
  inventory split returned by Snowflake and derives each AMT hex split from
  its grade-block lineage. APS sources use the fields mapped on Map Fields.
- **Calculate from user lump percentage** replaces the inventory and AMT split
  using the entered **CB Lump Percentage (%)**. Inventory stockpiles are
  calculated at stockpile level. AMT hexes are first consolidated using their
  valid mapped mass and grade pairs; the split is then calculated once for each
  submitted AMT chunk, after **AMT Stockpiles** and before **Database View**.
  If a chunk is subsequently rebuilt from its member hexes (for example during
  Database View refresh or project restoration), BlendMaster reapplies this
  chunk-level calculated split before exposing or scheduling the source; raw
  hex lump/fines values do not replace the calculated result.
  This avoids rejecting an entire source because an individual member hex has
  incomplete lineage while retaining the chunk-level coverage warning.

For user lump fraction `L` and fines fraction `F = 1 - L`:

```text
PROD1 lump WMT = total Product 1 WMT x L
PROD1 fines WMT = total Product 1 WMT x F
PROD1 lump DMT = total Product 1 DMT x L
PROD1 fines DMT = total Product 1 DMT x F
```

The adjusted head grade is the modelled Product 1 grade multiplied by the
ordinary SF regression reconciliation. The adjusted fines grade uses the
separate **SF - CBFL Campaign Fines** regression factor. The lump grade then
preserves the adjusted total-product metal balance:

```text
Ghead  = Gmodelled x standard SF regression
Gfines = Gmodelled x SF - CBFL Campaign Fines regression
Glump  = (Ghead x total product mass - Gfines x fines mass) / lump mass
```

The calculation prefers Product 1 DMT and uses Product 1 WMT only when DMT is
unavailable. It never splits insitu/ROM tonnes. Missing mapped product mass,
missing required product grades, or a negative/out-of-range back-calculated
lump grade blocks submission and identifies the invalid stockpile or AMT chunk
and analyte. Inventory validation occurs in Data Streams; AMT validation occurs
when AMT Stockpiles is submitted.

When **Enable Lump and Fines by-products** is selected, Data Streams also
requires explicit canonical fields for the Lump/Fines quantities and the five
Lump/Fines grades. Product Build Settings then operates two independent lanes:
one active Lump build and one active Fines build. Every transaction contributes
its configured portion to both active builds and must satisfy both build-grade
contracts. Rows are sequential within each lane. Completion of either active
build ends the steady state; the completed lane advances while the other lane
retains its current build and balance in the new steady state. Optimised and
manual reports expose both lane IDs, additions, grades and progress.

For imported Cloudbreak 2WP targets in by-product mode, `CBFL` rows populate
the Lump/FL lane and `CBSF` rows populate the Fines/SF lane. The paired rows
retain their independent target tonnes and grades; they are not merged into a
single build target.

### AMT spatial tonnage reconciliation

AMT opening tonnes are reconstructed at the exact inventory build active at
scenario start. Inbound and outbound AMT rows are deduplicated by `INTERNALID`;
inbound tonnes are positive and outbound tonnes are negative at their recorded
target/source hex.

Known source-hex precision can overdraw reclaimed hexes. BlendMaster corrects
this before chunking as follows:

1. Negative hexes form one or more reclaim fronts. Their principal row axis is
   calculated from centroid geometry and reclaim progression is taken
   perpendicular to that axis, toward the centroid of remaining positive ore.
2. Each deficit is transferred to positive hex capacity in deterministic
   nearest-first order. Connected adjacent hexes and the inferred progression
   direction are preferred; progressively more distant rows are used as needed.
3. Negative corrected hexes are set to zero. If the remaining hex total exceeds
   inventory, the residual deduction is biased toward poorer grade-block
   lineage using `hex WMT x (1 + 4 x (1 - lineage coverage))`. A zero-coverage
   hex therefore has five times the initial deduction weight of a fully covered
   hex. Per-hex deductions are capped at available WMT and spill into
   better-covered hexes when necessary. Inventory additions remain proportional
   to positive WMT. The final sum exactly equals authoritative inventory
   `BALANCEWMT`.

The UI retains raw signed tonnes, spatial and inventory adjustments—including
the per-hex inventory deduction and lineage coverage used—final tonnes,
unresolved deficits, direction and method/status fields. `FINAL_WMT`,
not raw tonnes, is used for AMT chunking. The spatial algorithm is tonnage-only;
grade-block composition is subsequently aligned to `FINAL_WMT` under the
documented proportional-depletion assumption.

On the AMT map, **Exclude / Restore Hexes** can deliberately remove individual
displayed hexes before automatic or manually directed chunk generation. These
hexes are excluded from axes, dig paths, chunks and solver sources; the chunk
goal seek uses remaining eligible AMT WMT. Generated chunks retain the excluded
hex IDs, count and WMT for audit and project reload. Clicking an excluded
red-cross marker again in exclusion mode restores it.

## Historical OPF factors

Only completed shift dates strictly before scenario start are used. Each brand
and analyte retains the shortest successful window in 7, 14, 21, 28, then 30
days. Daily blend factors are weighted by FEED_WMT; daily regression factors are
weighted by PROD_WMT. Missing brand/analyte results use an available OPF brand
when possible, otherwise factor 1.0 with a warning. Calculated and user-edited
effective factors are stored separately.

Cloudbreak has one additional editable factor per analyte named **SF - CBFL
Campaign Fines**. It is calculated only from CBSF assay rows on completed CB
shift dates that also contain a CBFL product row; the CBFL row's own regression
factor is never applied. Each analyte retains its shortest successful window in
7, 14, 21, 28, 30, then 60 days. If no paired CBSF+CBFL history exists within
60 days, BlendMaster uses that analyte's ordinary SF regression factor and
shows an explicit fallback warning.

The product-stream Planning Plan category defaults to `OPF Production`; ROM
streams default to `OPF Feed`. Both remain configurable for APS model variants.

## Field mappings

The Map Fields screen includes the first 24HR `Mining.csv` selector in the
workflow, reads its header row and presents the distinct APS fields beside the
mapping grid. The selected path is carried forward into Guidance Schedules.
The same browser pattern is used for raw Inventory and AMT fields, which become
available after Stockpile Inventories and the AMT opening fetch. Select a
mapping cell and either double-click a field or drag it onto that cell. This
avoids transcription errors in long Snowflake and APS process-stream names.
Only grade and additive tonnes fields appear in this browser.

The formerly separate APS grade/property tables are represented by the same
canonical mapping grid. The standard prepopulated catalogue includes:

| Property group | BlendMaster field names |
| --- | --- |
| Insitu / ROM | `modelled_rom_wmt`, `modelled_rom_dmt` |
| Ore Type | `oretype_<type>_wmt`, `oretype_<type>_dmt` for `bid`, `cidl`, `cidm`, `cidu`, `did`, `hc`, `other` |
| Product 1/2/3 | `prod<n>_wmt`, `prod<n>_dmt`, `prod<n>_minus_1mm_wmt`, `prod<n>_minus_1mm_dmt`, and product grade fields |
| CB Product 1 Split | `prod1_<size>_wmt`, `prod1_<size>_dmt`, and `prod1_<size>_<assay>` for size `fines` or `lump` and assay `fe`, `sio2`, `al2o3`, `p`, `mn`, `loi_425`, `loi_total`, `s`, `as` |

Mappings store the exact APS header but expose the stable BlendMaster name in
Database View and constraint expressions. An additive mapped total is prorated
to each APS payload by `payload tonnes / grouped source tonnes`; an intensive
value is copied unchanged. When payloads for the same grade block are
consolidated, additive properties are summed and every weighted-average field
uses its own additive **Weight Field** from Define Fields. A mapped field is
rejected when the same raw APS grade is configured with incompatible raw
weighting bases.

Map Fields is also the strict APS processing boundary. BlendMaster reads the
fixed schedule, destination and haulage columns required to construct APS
transactions, plus only the APS grade and source-property headers explicitly
mapped on this page. Other numeric columns remain available in the header
browser but are not loaded, grouped, shown in Database View, passed to the
solver or written to reports until they are mapped. This avoids treating APS
calendar, period, UID and other model-control columns as material properties.

When an APS payload is sent to a stockpile destination, every retained mapped
additive property is added to that inventory stockpile and every retained
weighted-average property is recalculated using its configured additive weight.
Only the portion actually sent to the destination is accumulated; a direct-tip
portion is excluded. A destination stockpile is represented as an Inventory
Stockpile even if its on-ground counterpart was previously available as AMT.
Later reclaim proportionally depletes additive balances while its weighted
averages remain unchanged until new material is built into it. APS modelled and
adjusted grades remain equal because APS inputs are already reconciled.

Raw fields that are not mapped do not become canonical source properties. A
defined but unmapped canonical field remains present with a blank value on
every Database View source. This is intentional: the gap is visible and can
make a selected grade or constraint infeasible instead of being replaced by
zero.

## Database View

Data Streams proceeds to AMT Stockpiles when any footprint uses AMT;
submitting its chunks opens **Database View**. Inventory-only scenarios open
Database View directly from Data Streams. This source-level audit
snapshot is reused by the next optimisation run. It includes:

- selected inventory stockpiles, excluding the duplicate inventory instance
  of a stockpile selected as AMT;
- every selected AMT chunk in reclaim sequence;
- one tonne-weighted source column per APS grade block whose movement falls
  inside the configured planning horizon; and
- every flattened grade stream plus the selected stream vector and per-analyte
  fallback provenance for each configured brand.

The table is transposed for source-to-source comparison: fields are rows and
sources are columns. It intentionally excludes payload-level movement,
destination, calendar and solver-configuration fields. It is a focused audit
of the source tonnes and grades entering scheduling.

Database View contains every field created in Define Fields for every source.
A mapped/calculated value is shown when available; an unmapped or unavailable
value is blank. Once Solver Configuration is submitted, the run projects that
catalogue down to fields checked **Use in Optimisation**, plus any dependencies
already referenced by a saved enabled custom expression. The five selected
grades remain available separately for period/brand selection. This reduces
steady-state build and depletion work without removing fields from Database
View.

The custom-constraint field picker lists the built-in solver fields and the
canonical rows checked **Use in Optimisation**. Being listed does not imply
complete coverage: before solving, every selected source that could participate
is checked for every referenced field. Preparation names the constraint and
source when a value is missing, non-numeric or non-finite; missing data is not
replaced with zero. Coverage fields in Database View should be reviewed before
selecting a partially populated property.

### Choosing visible fields and sources

**Choose Fields...** opens a searchable checklist of every field currently
available in the table. Each chosen field becomes a row. The default selection
keeps the scheduling inputs
compact:

- source identity (`source_type`, `source_id`, `parent_stockpile`,
  `build_or_chunk` and AMT `sequence`);
- canonical `modelled_rom_wmt`, `modelled_rom_dmt` and `selected_stream`;
- all canonical `insitu_<analyte>` fields; and
- the five canonical grade fields for the optimiser grade stream selected on
  Data Streams (for example, `adjusted_product_fe`).

Use **Select All** to expose every canonical field defined in Define Fields,
plus the automatic lineage audit fields, or **Defaults** to return to the
compact view.
Coverage rows are audit fields and are hidden from the checklist by default;
enable **Show coverage fields (audit / troubleshooting)** to make every
`*_coverage_pct` row selectable. This switch changes presentation only and is
retained with scenario and project state. Searching only filters the checklist;
it does not remove fields from the underlying source record. If saved fields
are unavailable in a newly loaded dataset they are simply omitted, while newly
available fields remain accessible through the selector.

**Choose Sources...** opens a second searchable checklist. All sources are
selected by default; clear any inventory stockpile, AMT chunk or APS grade block
that is not needed for the current comparison. Source choices are also retained
with scenario and project state, and newly appearing sources are selected by
default. The **Source Type** and source search controls provide a temporary
display filter by hiding matching source columns; they do not change the saved
source selection. Both the checklist and temporary filters are presentation
controls only: hiding a column does not remove that source from scheduling.

### Database View field dictionary

The **Field** column uses the exact canonical BlendMaster names from Define
Fields. Raw Snowflake/APS names, physical aliases such as `source_wmt` and
`tonnes`, and internal implementation metadata are not displayed as parallel
rows. The only non-definition data rows are source identity/status and the
automatic lineage/coverage audit metrics described below.

Displayed sum quantities (tonnes, WMT and counts) are rounded to whole units.
Displayed weighted-average grades, modelled properties and coverage values are
rounded to two decimal places. The underlying calculation values retain their
full precision.

#### Source identity and quantity

| Field | Meaning | When a blank is expected |
| --- | --- | --- |
| `source_type` | `Inventory Stockpile`, `AMT Chunk`, `AMT Stockpile - No Chunks`, or `APS Grade Block`. | Never for a valid source. |
| `source_id` | Inventory stockpile name, AMT chunk/hex identifier, or APS grade-block source name. | Never for a valid source. |
| `parent_stockpile` | The stockpile footprint that owns an inventory source or AMT chunk. | APS grade blocks do not have a parent stockpile. |
| `build_or_chunk` | Inventory build name for an inventory source; chunk/hex identifier for an AMT source. | APS grade blocks and the AMT no-chunks warning source. |
| `sequence` | AMT reclaim sequence number. | Inventory stockpiles and APS grade blocks. |
| `modelled_rom_wmt` | Canonical insitu/ROM wet-tonne balance: selected inventory balance, selected AMT chunk balance, or the APS grade-block quantity inside the planning horizon. | Blank when this required field has not been mapped or derived. |
| `modelled_rom_dmt` | Canonical insitu/ROM dry-tonne balance. | Blank when this required field has not been mapped or derived. |

APS payloads are consolidated into one grade-block source column. Its grades are
independently tonne-weighted per analyte, so a missing analyte does not prevent
the available analytes from being shown.

#### AMT grade-block lineage audit

| Field | Meaning | When a blank is expected |
| --- | --- | --- |
| `lineage_entry_count` | Number of lineage records, including an unmatched record when present. | Non-AMT sources. |
| `lineage_inbound_wmt` | Inbound WMT represented by grade-block lineage for the hex. | Non-AMT sources, or an AMT hex with no inbound lineage. |
| `lineage_matched_wmt` | Lineage WMT attributed through EXPIT or the AMT truck list. | Non-AMT sources. |
| `lineage_unmatched_wmt` | Inbound WMT retained without a defensible grade-block match. | Non-AMT sources; zero is preferred for AMT. |
| `lineage_final_wmt` | Final spatially and inventory-reconciled WMT to which the lineage composition was aligned. | Non-AMT sources. |
| `lineage_matched_final_wmt` | Final WMT represented by the matched lineage share. | Non-AMT sources. |
| `lineage_unmatched_final_wmt` | Final WMT represented by the unmatched lineage share. | Non-AMT sources; zero is preferred for AMT. |
| `lineage_coverage_pct` | Percentage of lineage inbound WMT attributed through EXPIT or the AMT truck list. | Non-AMT sources or a hex with no inbound lineage. |
| `grade_block_count` | Number of distinct resolved grade-block identities contributing to the hex, excluding `UNMATCHED`. | Non-AMT sources. |
| `<property>` | Canonical grade-block-lineage-weighted physical property, shown only when the same name exists in Define Fields; examples include `oretype_bid_wmt` and `prod1_minus_1mm_wmt`. | Non-AMT sources, an unmapped source family, or when no contributing lineage supplies that property. |
| `<property>_coverage_pct` | Percentage of final hex tonnes supporting the corresponding property. Hidden by default; enable the coverage checkbox in **Choose Fields...** for audit/troubleshooting. | Non-AMT sources or a zero-tonne hex. |

The inventory match now selects the correct build and total; it no longer
provides AMT internal blend or upgrade factors. Modelled product grades can feed
the product stream, while lineage identities, physical properties and coverage
remain diagnostic/model-input provenance. A
positive hex with incomplete lineage is actionable and is recorded in the
`warnings` field. For generated chunks, repeated per-hex product messages are
replaced by one WMT-weighted, fixed-format summary:

```text
Lineage: OK | Mapped fields: Partial - product_wmt 96.54% | Product grades: Partial - PROD1 Fe 92.29% | Fallbacks: None | Geometry: OK
```

Each section is always present and reports `OK`, `Partial`, `Missing`, `None`
or `Not applicable` as appropriate. Partial
coverage of the active product channel is warned: the displayed modelled grade is based on covered lineage tonnes, while
the coverage field quantifies the excluded share. During chunk/source
aggregation, product mass without a valid grade is excluded from that grade's
weighted average rather than nullifying the complete stream. The corresponding
brand-specific adjusted grade retains its historical regression ratio and is
recalculated from the final covered-mass modelled grade. Uncovered tonnes still
participate in chunking, so the coverage warning remains an important statement
that the covered grade is being extrapolated over the complete source balance.

#### Grade fields

The analyte suffixes are `fe`, `si`, `al`, `p` and `mn`. In field names, `si`
means SiO2 and `al` means Al2O3.

| Field pattern | Meaning | Required by the optimiser? |
| --- | --- | --- |
| `insitu_<analyte>` | Canonical insitu/source grade. | It is available to optimisation when selected as the optimiser grade stream. |
| `modelled_rom_<analyte>` | Canonical modelled ROM grade. | It is available to optimisation when selected as the optimiser grade stream. |
| `adjusted_rom_<analyte>` | Modelled ROM grade after historical blend reconciliation. | It is available to optimisation when selected as the optimiser grade stream. |
| `modelled_product_<analyte>` | Canonical modelled product grade. | It is available to optimisation when selected as the optimiser grade stream. |
| `adjusted_product_<analyte>` | Modelled product grade after historical regression reconciliation. | It is available to optimisation when selected as the optimiser grade stream. |
| `selected_stream` | The single stream selected on Data Streams for this run. | Yes; it controls which stored vector is requested. |

Inventory and AMT modelled ROM is physically unbranded, while APS can map a
different ROM input for each brand. Brand resolution remains part of the grade
stream data behind the canonical row. Database View deliberately avoids
publishing additional raw `grade_*` aliases alongside the five canonical grade
families, so one meaning has one visible field name.

When an older project contains saved AMT chunks, BlendMaster preserves each
chunk's membership and sequence, then refreshes its mapped tonnes/grades from
the current member hexes and recalculates branded adjusted streams. The
restore, Data Streams, Database View and solver boundaries all check the same
input signature, but the expensive member-hex rebuild runs only once while the
opening snapshot, mappings, factors, by-product settings and chunk membership
remain unchanged. `hex_sequence_table_argument` is a deep solver snapshot of
that one canonical result; it is not independently rebuilt. Older projects
without a saved signature perform one compatibility rebuild and then use the
same guarded path.

Not every raw stream field is used at the same time. BlendMaster retains all
five so the selected result can be traced and the user can switch streams
without losing the underlying calculations. The `selected_...` fields are
therefore derived from the wider `grade_...` set, but are intentionally kept
because they show the exact effective vector after fallback.

#### Warnings and cell colours

| Appearance/field | Meaning | Action |
| --- | --- | --- |
| Pale yellow `fallback_...` cell | A fallback **was used** for that brand/analyte. The cell contains the requested and substituted stream/brand; hover to see the full text. | Review whether the substitution is acceptable. It is not itself a missing required cell. |
| Blank `fallback_...` cell with the normal background | No fallback was required. | None; this is the preferred state. |
| Blank pale red/pink `grade_...` or `selected_...` cell | No value is stored/resolved for that exact field. | Check it when it belongs to the selected stream and active brand. It can be expected for streams or brands that do not apply to the source. |
| `warnings` | Consolidated source-calculation and reconciliation messages. AMT product coverage is summarized for the whole chunk, and analytes that use the same fallback route are grouped into one brand-level fallback message. | Review any message that affects the active brand or selected stream. |

The UI does not colour an empty fallback cell yellow. A yellow cell always has
fallback provenance, although its text can be easier to read from the tooltip.

### Which blanks matter?

Only a small subset of the full source record is essential to one scheduling decision:
the source identity, available tonnes, and the five `selected_<active
brand>_<analyte>` values. A blank selected grade is not necessarily fatal
because individual constraints may not use every analyte, but it must not be
silently interpreted as a grade of zero.

The remaining blanks fall into these expected categories:

- source-specific structure: APS sources have no stockpile/build/reconciliation
  fields, and inventory sources have no AMT sequence or match fields;
- unused streams: dry plants, APS mappings, or a particular source may not
  carry every possible intermediate stream/brand combination;
- audit-only provenance: blank `fallback_...` fields mean no fallback occurred;
  and
- unavailable data: pale red/pink grade cells and warnings identify cases that
  need review when they intersect the selected stream and active brand.

Some fields are redundant for solving but not for diagnosis. Raw grades,
intermediate streams, match metadata, fallback provenance and warnings can be
ignored by the optimiser, yet they explain exactly how the effective selected
grades were produced. They remain in the Database View record even when hidden
by the field selector, and can be exposed when the data-stream calculations
need diagnosis.

## Audit and reporting

Inventory and AMT setup views show the calculated streams for every configured
brand. AMT sources also retain the selected inventory build/balance timestamp, raw
and spatially reconciled tonnes, grade-block lineage, per-property coverage and
lineage warnings. The obsolete inventory-derived internal blend and upgrade
factors are not part of the AMT stream calculation.

Optimised and manual blend reports retain the selected stream and brand, the
five grades actually used by the solver, and all five analytes for every raw
stream as `source_grade_<stream>_<analyte>`. The Optimised Blend Sequence
transaction table exposes the same fields, allowing a reported decision to be
traced back through adjusted product, modelled product, adjusted ROM, modelled
ROM and insitu values without re-running the model.

Fields checked **Use in Optimisation** (and fields referenced by a retained
enabled custom expression) are written to optimised and manual reports as
`source_property_<canonical_field>`. Intensive properties retain the source
value. Additive properties are scaled to the tonnes selected on that report row,
so, for example, `source_property_prod1_wmt` is the allocated Product 1 WMT,
not the whole opening-source total. Unchecked physical properties remain
available in Database View but are deliberately omitted from solve state and
reports to avoid unnecessary build, depletion and database work.

For each active additive field, reports expose the full transaction audit:

- `source_property_<field>_opening_balance`: the amount available to that
  solver event before selection;
- `source_property_<field>_closing_balance`: opening less actual depletion;
- `source_property_<field>`: the amount consumed by the transaction,
  proportionally depleted against ROM WMT.

`modelled_rom_wmt` is the single user-facing canonical insitu/ROM WMT. The
balance tracker derives its internal physical source balance from this field
at source initialization, after builds, after ordinary reclaims and whenever
the active AMT chunk changes. If restored properties are
at a different mass scale from the active source/chunk balance, BlendMaster
first rescales every additive property by `active balance / saved ROM WMT`.
Other additive fields, including ROM DMT, product mass, ore-type tonnes and
ultrafines tonnes, therefore retain their mapped ratios and deplete in
proportion to actual ROM WMT.

Custom constraints are whole-steady-state rules. Additive expressions sum the
proportionally depleted field across selected sources. Weighted-average
expressions use the additive Weight Field declared in Define Fields across the
selected sources. A literal `1` is the scalar one, not selected ROM tonnes.
Thus `quantity_a / quantity_b` is `sum(quantity_a) / sum(quantity_b)`, while
`quantity_a / 1` is simply `sum(quantity_a)`. Missing mapped values or missing
weighted-average dependencies stop optimisation instead of falling back.

In reports, unqualified `numerator`, `denominator` and `actual_ratio` are the
whole-blend aggregates repeated on each row. For additive sides, a source
coefficient is contribution per physical source WMT and source contribution is
the selected additive amount. For weighted-average sides, the coefficient is
the source value and contribution is its weighted mass; the aggregate divides
the summed weighted mass by the summed selected declared weight. Scalar sides
have blank source coefficients, zero source contributions and their literal in
the unqualified aggregate column.
