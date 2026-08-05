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
Site Configuration -> Stockpile Inventories -> Define Fields -> Map Fields
-> Data Streams -> Guidance Schedules -> AMT Stockpiles (when selected)
-> Database View
```

**Stockpile Inventories** is intentionally pre-calculation. It shows source
selection, inventory identity/build, opening balance and insitu assays. The
modelled and adjusted fields are not treated as defined until the next steps.

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
analytes, `source_wmt`, `modelled_product_wmt` and
`modelled_product_dmt`, plus `modelled_rom_wmt` and `modelled_rom_dmt`.
Insitu/modelled/adjusted ROM grades use `modelled_rom_wmt`; modelled/adjusted product
grades use `modelled_product_dmt`. Map the product fields to the active OPF
product channel's per-source Product 1/2/3 WMT/DMT. `source_wmt` mirrors
`modelled_rom_wmt`, because ROM WMT is the opening insitu balance. Required
fields may be left unmapped; they then remain blank and traceable rather than
being silently removed from the source schema.

**Use in Optimisation** is the compute/report gate. Checked properties are
available in the custom-constraint field picker, carried through build,
depletion and event generation, and written to optimised/manual reports.
The additive Weight Field behind a required or checked weighted-average field
is checked automatically because it is part of the same calculation. Other
unchecked properties stop at Database View. The five effective selected grades
are always sent to optimisation independently of this checkbox.

The checkbox does not manufacture a value or mapping. For example,
`modelled_rom_dmt` must be mapped to the applicable Inventory/AMT `feed_dmt`
field. If it is checked but unmapped, its report columns are deliberately
present and blank; a custom constraint that requires it cannot obtain a valid
coefficient. This makes the data gap distinguishable from a genuine zero.

**Map Fields** maps raw Inventory, AMT and APS columns onto the canonical rows.
Choose a source family (and, for APS, an optional brand), then double-click or
drag an available raw field into **Source Field**. The page owns the 24HR
`Mining.csv` selector; Guidance Schedules reuses the same path. Mappings save
the exact raw header while every downstream component uses only the stable
BlendMaster field name. Older projects migrate their former APS mappings and
receive explicit compatibility mappings for the established inventory and AMT
stream inputs.

AMT mappings apply independently to each imported **hex** before chunks are
formed. The AMT source list includes lineage-derived per-hex fields, including
`prod1_wmt`, `prod1_dmt`, `prod2_wmt` and `prod2_dmt` where the contributing
grade blocks supply explicit modelled product tonnes, including Product 3.
It also includes **ROM / opening stockpile WMT/DMT** (stored raw as
`feed_wmt`/`feed_dmt` for compatibility); map these to
`modelled_rom_wmt`/`modelled_rom_dmt`. Map the active product channel to
`modelled_product_wmt` and `modelled_product_dmt`; the product-grade fields
then use that DMT denominator when hexes are consolidated into chunks. The
same canonical mass mappings are available for inventory stockpiles, whose
`feed_wmt` is their opening balance and whose `feed_dmt` is its dry equivalent.

The required `insitu_*` fields are visible in Map Fields for Inventory, AMT
and APS. They default to the established raw assay fields during migration but
remain explicit and user-editable like other source mappings.

`adjusted_rom_*` and `adjusted_product_*` are deliberately absent from Map
Fields. They are calculated fields: adjusted ROM is modelled ROM multiplied by
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

- ROM/opening-stockpile WMT and DMT (stored raw as `feed_wmt` and `feed_dmt`);
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
ROM/opening WMT (`feed_wmt`) = BALANCEWMT
ROM/opening DMT (`feed_dmt`) = BALANCEWMT x (1 - insitu moisture)
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
  using the entered **CB Lump Percentage (%)**. This is useful when the
  grade-block-derived split is unavailable or the scenario needs a controlled
  assumption.

For user lump fraction `L` and fines fraction `F = 1 - L`:

```text
PROD1 lump WMT = source WMT x L
PROD1 fines WMT = source WMT x F
PROD1 lump DMT = source DMT x L
PROD1 fines DMT = source DMT x F
```

When a total Product 1 assay `Gtotal` and an independent fines assay `Gfines`
are available, the lump assay preserves the total product metal balance:

```text
Glump = (Gtotal x total product mass - Gfines x fines mass) / lump mass
```

The calculation prefers DMT and uses WMT when DMT is unavailable. If no
independent fines assay is available, both lump and fines inherit the total
Product 1 assay; this is the determinate mass-conserving fallback and is
recorded in `cb_split_warning`. The provenance is exposed as
`cb_split_method`; a negative back-calculated lump assay is retained to
preserve the mass balance and is explicitly warned.

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
3. Negative corrected hexes are set to zero. The residual stockpile-level
   difference, including unattributed movements and inventory adjustments, is
   applied proportionally to the remaining positive hexes so their sum exactly
   equals the authoritative inventory `BALANCEWMT`.

The UI retains raw signed tonnes, spatial and inventory adjustments, final
tonnes, unresolved deficits, direction and method/status fields. `FINAL_WMT`,
not raw tonnes, is used for AMT chunking. The spatial algorithm is tonnage-only;
grade-block composition is subsequently aligned to `FINAL_WMT` under the
documented proportional-depletion assumption.

## Historical OPF factors

Only completed shift dates strictly before scenario start are used. Each brand
and analyte retains the shortest successful window in 7, 14, 21, 28, then 30
days. Daily blend factors are weighted by FEED_WMT; daily regression factors are
weighted by PROD_WMT. Missing brand/analyte results use an available OPF brand
when possible, otherwise factor 1.0 with a warning. Calculated and user-edited
effective factors are stored separately.

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

The formerly separate APS grade/property tables are represented by the same
canonical mapping grid. The standard prepopulated catalogue includes:

| Property group | BlendMaster field names |
| --- | --- |
| Feed | `feed_dmt`, `feed_moisture` |
| Ore Type | `oretype_<type>_wmt`, `oretype_<type>_dmt` for `bid`, `cidl`, `cidm`, `cidu`, `did`, `hc`, `other` |
| Product 1/2/3 | `prod<n>_wmt`, `prod<n>_dmt`, `prod<n>_mass_recovery`, `prod<n>_moisture`, `prod<n>_minus_1mm_pct`, `prod<n>_minus_1mm_wmt`, `prod<n>_minus_1mm_dmt` |
| CB Product 1 Split | `prod1_<size>_yield_pct`, `prod1_<size>_wmt`, `prod1_<size>_dmt`, `prod1_<size>_moisture`, and `prod1_<size>_<assay>` for size `fines` or `lump` and assay `fe`, `sio2`, `al2o3`, `p`, `mn`, `loi_425`, `loi_total`, `s`, `as` |

Mappings store the exact APS header but expose the stable BlendMaster name in
Database View and constraint expressions. An additive mapped total is prorated
to each APS payload by `payload tonnes / grouped source tonnes`; an intensive
value is copied unchanged. When payloads for the same grade block are
consolidated, additive properties are summed and intensive properties are
WMT-weighted.

Raw fields that are not mapped do not become canonical source properties. A
defined but unmapped canonical field remains present with a blank value on
every Database View source. This is intentional: the gap is visible and can
make a selected grade or constraint infeasible instead of being replaced by
zero.

## Database View

Guidance Schedules proceeds to AMT Stockpiles when any footprint uses AMT;
submitting its chunks opens **Database View**. Inventory-only scenarios open
Database View directly from Guidance Schedules. This source-level audit
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
- available `tonnes` and `selected_stream`;
- all stored insitu grade fields; and
- every `selected_<brand>_<analyte>` field, which is the effective grade vector
  after stream and per-analyte fallback resolution.

Use **Select All** to expose reconciliation, lineage, intermediate streams and
extended source properties, or **Defaults** to return to the compact view.
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

The table can contain many field rows because it shows both the values supplied
to the optimiser and the intermediate values used to derive them. Fields fall
into four groups.

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
| `tonnes` | Selected inventory balance, selected AMT chunk balance, or the sum of APS payload tonnes for that grade block inside the planning horizon. | A valid scheduling source should not be blank. The AMT no-chunks warning source intentionally shows zero. |

APS payloads are consolidated into one grade-block source column. Its grades are
independently tonne-weighted per analyte, so a missing analyte does not prevent
the available analytes from being shown.

#### AMT opening and grade-block lineage

| Field | Meaning | When a blank is expected |
| --- | --- | --- |
| `internal_recon_matched` | Legacy field name indicating that the AMT footprint was matched to an inventory build/balance. It does not mean internal blend or upgrade factors were used. | All inventory and APS sources. |
| `matched_inventory_stockpile` | Inventory stockpile used to identify the AMT footprint instance and authoritative total. | All non-AMT sources, or an unmatched AMT source. |
| `matched_inventory_build` | Exact inventory build used as the AMT `LOCATION_NAME`. | All non-AMT sources, or an unmatched AMT source. |
| `matched_inventory_time` | Timestamp of the authoritative inventory balance at or before scenario start. | All non-AMT sources, or an unmatched AMT source. |
| `raw_wmt` | Signed inbound-minus-outbound AMT balance before spatial correction. | Non-AMT sources. |
| `spatially_corrected_wmt` | Nonnegative balance after directional deficit allocation. | Non-AMT sources. |
| `spatial_adjustment_wmt` | Change caused by transferring AMT overdraw to nearby positive donor hexes. | Non-AMT sources. |
| `ledger_adjustment_wmt` | Final proportional change required to match inventory `BALANCEWMT`. | Non-AMT sources. |
| `geometry_quarantine_count` | Number of AMT hexes in the chunk whose remote or missing coordinate was excluded from spatial path generation. | Non-AMT sources, or zero when all chunk hexes are positioned normally. |
| `geometry_quarantine_wmt` | WMT retained through non-spatial chunk allocation after coordinate quarantine. | Non-AMT sources, or zero when no coordinate was quarantined. |
| `geometry_quarantine_hexes` | Comma-separated IDs of the quarantined hexes assigned to the chunk. | Non-AMT sources, or a normal AMT chunk. |
| `lineage_entry_count` | Number of lineage records, including an unmatched record when present. | Non-AMT sources. |
| `lineage_inbound_wmt` | Inbound WMT represented by grade-block lineage for the hex. | Non-AMT sources, or an AMT hex with no inbound lineage. |
| `lineage_matched_wmt` | Lineage WMT attributed through EXPIT or the AMT truck list. | Non-AMT sources. |
| `lineage_unmatched_wmt` | Inbound WMT retained without a defensible grade-block match. | Non-AMT sources; zero is preferred for AMT. |
| `lineage_final_wmt` | Final spatially and inventory-reconciled WMT to which the lineage composition was aligned. | Non-AMT sources. |
| `lineage_matched_final_wmt` | Final WMT represented by the matched lineage share. | Non-AMT sources. |
| `lineage_unmatched_final_wmt` | Final WMT represented by the unmatched lineage share. | Non-AMT sources; zero is preferred for AMT. |
| `lineage_coverage_pct` | Percentage of lineage inbound WMT attributed through EXPIT or the AMT truck list. | Non-AMT sources or a hex with no inbound lineage. |
| `grade_block_count` | Number of distinct resolved grade-block identities contributing to the hex, excluding `UNMATCHED`. | Non-AMT sources. |
| `<property>` | Canonical grade-block-lineage-weighted physical property, using the same name as its Inventory/APS counterpart; examples include `oretype_bid_wmt` and `prod1_minus_1mm_wmt`. Database View does not add a second `modelled_` prefix. | Non-AMT sources, or when no contributing lineage supplies that property. |
| `<property>_coverage_pct` | Percentage of final hex tonnes supporting the corresponding property. Hidden by default; enable the coverage checkbox in **Choose Fields...** for audit/troubleshooting. | Non-AMT sources or a zero-tonne hex. |
| `cb_split_method` | Whether the CB split was grade-block-derived, unavailable, calculated with a back-calculated lump grade, or calculated with the equal-grade fallback. | Non-CB sources. |
| `cb_split_warning` | Warning produced by the optional calculated CB split, including unavailable independent fines assays or a negative back-calculated lump assay. | Blank for a complete derived split or a calculated split requiring no warning. |

The inventory match now selects the correct build and total; it no longer
provides AMT internal blend or upgrade factors. Modelled product grades can feed
the product stream, while lineage identities, physical properties, match
coverage and spatial adjustments remain diagnostic/model-input provenance. A
positive hex with incomplete lineage is actionable and is recorded in the
`warnings` field. Partial coverage of the active product channel is also
warned: the displayed modelled grade is based on covered lineage tonnes, while
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
| `grade_<analyte>` | Raw/legacy source grade retained as the final per-analyte fallback and as an audit reference. It normally corresponds to the original insitu/source grade. | Only used when the requested stream and all upstream stream fallbacks are unavailable for that analyte. |
| `grade_<stream>_<analyte>` | Stored unbranded grade for one of `insitu`, `modelled_rom`, `adjusted_rom`, `modelled_product`, or `adjusted_product`. | It is an intermediate/audit value unless it resolves the selected stream for the active brand. |
| `grade_<stream>_<brand>_<analyte>` | Stored brand-specific grade stream. Brand names are lower-cased and non-alphanumeric characters become underscores; for example, brand `CCFB` produces `grade_adjusted_product_ccfb_fe`. | It is an intermediate/audit value unless that brand and stream are selected. |
| `selected_stream` | The single stream selected on Data Streams for this run. | Yes; it controls which stored vector is requested. |
| `selected_<brand>_<analyte>` | The effective grade resolved for that brand and analyte after applying the fallback chain. These are the clearest Database View representation of the grades that scheduling will use when that brand is active. | Yes, for the brand being produced. |
| `fallback_<brand>_<analyte>` | Provenance of a fallback, for example `adjusted_product[CCFB] -> adjusted_rom[CCFB]`. A blank means the requested stream/brand value was available and no fallback was needed. | No. This is audit information explaining how the corresponding `selected_...` value was obtained. |

Inventory and AMT modelled ROM is physically unbranded. Database View retains
that source vector as `grade_modelled_rom_<analyte>` and also publishes an
identical `grade_modelled_rom_<brand>_<analyte>` copy for every configured
brand. APS modelled ROM uses the same branded field names, but each brand can
contain a distinct value from its mapped APS header. Historical blend
reconciliation is applied only when producing branded adjusted ROM; it never
changes modelled ROM.

When an older project contains saved AMT chunks, BlendMaster preserves each
chunk's membership, sequence, tonnes and modelled grades, then refreshes the
branded modelled-ROM copies and recalculates adjusted ROM/product grades from
the current editable historical factors. This upgrade runs during project
restore, Data Streams submission, Database View refresh and immediately before
solving, so a legacy blank adjusted-product vector does not force an obsolete
fallback to modelled product.

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
| `warnings` | Consolidated source-calculation, reconciliation and per-analyte fallback messages for the source. | Review any message that affects the active brand or selected stream. |

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

`source_wmt` and `modelled_rom_wmt` are canonical ROM WMT and are synchronized
to the balance tracker at source initialization, after builds, after ordinary
reclaims and whenever the active AMT chunk changes. If restored properties are
at a different mass scale from the active source/chunk balance, BlendMaster
first rescales every additive property by `active balance / saved ROM WMT`.
Other additive fields, including ROM DMT, product mass, ore-type tonnes and
ultrafines tonnes, therefore retain their mapped ratios and deplete in
proportion to actual ROM WMT.

Custom-constraint report fields have three distinct levels. A
`source_*_coefficient` is the expression value per source WMT used by the
linear solver. A `source_*_contribution` is that coefficient multiplied by the
source's actual selected WMT. The unqualified `numerator` and `denominator`
are whole-blend totals (and are consequently repeated on each row of the same
blend); `actual_ratio` is their ratio. Thus, for a denominator of
`modelled_rom_wmt`, each source coefficient is normally 1.0,
each source denominator contribution is its actual depleted ROM WMT, and the
blend denominator is the sum of all source contributions. It should not be
expected to equal one row's `source_property_modelled_rom_wmt` unless the
blend uses only that source.
