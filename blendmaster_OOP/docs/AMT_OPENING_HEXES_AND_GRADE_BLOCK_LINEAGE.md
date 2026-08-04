# AMT Opening Hexes and Grade-Block Lineage

This document describes how BlendMaster reconstructs opening AMT hex tonnes,
reconciles AMT source-hex precision errors, and attributes the final material in
each hex to the grade blocks that supplied it. The opening query is evaluated at
the scenario start time. The inventory build ledger is the stockpile-total
authority; AMT supplies the spatial movement allocation.

The grade-block attribution produces **modelled** properties. It does not replace
the existing AMT insitu grades. Historical OPF blend and regression
reconciliation remain separate, brand-aware adjustment layers.

## Snowflake data used

The opening operation uses six Snowflake objects:

| Snowflake object | Purpose | Principal columns |
| --- | --- | --- |
| `AA_OPERATIONS_MANAGEMENT.SELFSERVICE.INVENTORY_STOCKPILE_TRANSACTIONS` | Select the stockpile build active at scenario start and obtain the authoritative opening balance. | `STOCKPILENAME`, `STOCKPILEBUILDNAME`, `TRANSACTIONDATETIME`, `BALANCEWMT` |
| `AA_OPERATIONS_MANAGEMENT.SLN_AMT.AMT` | Reconstruct inbound and outbound movements at trip and hex grain. | `INTERNALID`, `TARGETLOCATIONNAME`, `SOURCELOCATIONNAME`, `TARGETHEX`, `SOURCEHEX`, `DUMPEDDATETIME`, `TONNES`, target/source coordinates |
| `AA_OPERATIONS_MANAGEMENT.SLN_AMT.AMT_HEX_GRADES` | Supply the existing AMT insitu grades and hex coordinates. | `FOOTPRINT`, `LOCATION_NAME`, `HEX`, `FE`, `SIO2`, `AL2O3`, `P`, `MN`, `LATITUDE`, `LONGITUDE`, `LAST_UPDATE` |
| `AA_OPERATIONS_MANAGEMENT.SLN_AMT.AMT_STOCKPILE_HEX_TRUCK_LIST` | Link inbound dumps to a grade-block name and provide truck-list chemical properties and `ROM_MATS`. | `LOCATION_NAME`, `HEX`, `DUMPEDDATETIME`, `TRUCK_WMT`, `GRADE_BLOCK`, `ROM_MATS`, `GBI`, chemical-grade columns, `LAST_UPDATE` |
| `AA_OPERATIONS_MANAGEMENT.SELFSERVICE.INVENTORY_EXPIT_REHANDLE_TRANSACTIONS` | Link the same inbound trip to its numeric grade-block ID and modelled feed/product properties. | `INTERNAL_ID`, `SOURCE_GRADEBLOCK_ID`, `SOURCE_FMS`, `WMT_REPORTING`, `TRANSACTION_DATETIME`, feed properties, `PROD1_*`, `PROD2_*`, `IS_DELETED`, `DISCRIMINATOR` |
| `DA_OPERATIONS.STG_GRADECONTROL.GRADE_BLOCKS` | Supply Product 1 minus-1-mm and CB lump/fines model properties for the resolved inbound grade block. | grade-block name components used to form `FULL_NAME_WITH_SITE`, `PROD1_MINUS1MM_PCT`, `PROD1_FINES_*`, `PROD1_LUMP_*`, `GB_DRY_DENSITY`, `LOI_425` |

`AMT_STOCKPILE_HEX_MAP_AS_BUILD` is not required by this opening calculation.
The exact build is selected from inventory and the hex universe comes from AMT
movements plus `AMT_HEX_GRADES`.

## Processing sequence

### 1. Establish the as-of time

The scenario start is interpreted explicitly as Perth time:

```text
TO_TIMESTAMP_TZ('<scenario start> +08:00',
                'YYYY-MM-DD HH24:MI:SS TZH:TZM')
```

Inventory, AMT movement, truck-list and EXPIT rows later than this timestamp are
excluded. This prevents completed scheduling decisions from using future
movements.

### 2. Select the exact footprint instance

The caller can supply either a footprint name or a complete build name.
BlendMaster finds the latest inventory transaction at or before scenario start:

```text
requested build = STOCKPILEBUILDNAME
              or STOCKPILENAME
```

The selected `STOCKPILEBUILDNAME` becomes the exact AMT `LOCATION_NAME`. AMT
movements belonging to another build instance of the same physical stockpile are
therefore excluded. `BALANCEWMT` from the selected inventory row is the
authoritative stockpile total.

### 3. Reconstruct signed AMT movement tonnes

Inbound movements satisfy:

```text
AMT.TARGETLOCATIONNAME = selected LOCATION_NAME
AMT.DUMPEDDATETIME <= scenario start
AMT.TONNES > 0
```

Inbound tonnes are added to `TARGETHEX`. Outbound movements use the corresponding
`SOURCELOCATIONNAME` and are subtracted from `SOURCEHEX`.

AMT can repeat a trip over multiple rows. Rows are partitioned by
`LOCATION_NAME + INTERNALID`; the repeated `TONNES` values are averaged and one
latest row is retained. Consequently, each movement contributes once.

For hex `h`:

```text
raw_signed_wmt[h] = sum(deduplicated inbound WMT to h)
                  - sum(deduplicated outbound WMT from h)
```

An internal rehandle within the same footprint is both outbound from its source
hex and inbound to its target hex, so it changes spatial allocation but has zero
net effect on the footprint total. Movements without a usable source or target
hex are retained as `__UNATTRIBUTED__` for total-level reconciliation rather
than silently discarded.

### 4. Build the opening hex universe

The visible universe is the union of:

- hexes present in signed AMT movements; and
- hexes present in `AMT_HEX_GRADES` for the selected `LOCATION_NAME`.

The latest returned `AMT_HEX_GRADES` row per `LOCATION_NAME + HEX` supplies Fe,
SiO2, Al2O3, P and Mn insitu grades. Movement coordinates are a fallback when
the grade table has no coordinates. The movement reconstruction changes tonnes,
not these insitu grade values.

## Spatial tonnage reconciliation

AMT reclaim source-hex precision can assign too much reclaim to one hex while
adjacent hexes retain too much positive material. BlendMaster treats a negative
raw hex balance as a reclaim-attribution deficit, not as schedulable negative
ore.

The reconciliation is deterministic:

1. Calculate positive donor capacity and negative deficits.
2. Build connected geometry from easting/northing, falling back to
   latitude/longitude when necessary.
3. Infer the negative reclaim-row axis from weighted geometry. Reclaim
   progression is perpendicular to that row and points toward the centroid of
   remaining positive material.
4. Satisfy each deficit from positive capacity, preferring connected, nearby
   hexes in the inferred progression direction. More distant rows are used only
   when nearer capacity is insufficient.
5. Do not pass any negative balance into chunking. Retain unresolved deficits in
   audit fields.
6. Scale the remaining nonnegative hex balances proportionally so their sum
   equals inventory `BALANCEWMT` exactly.

The resulting tonnage stages are:

```text
RAW_WMT
  -> SPATIALLY_CORRECTED_WMT
  -> FINAL_WMT
```

`FINAL_WMT` drives AMT chunking and optimisation. Raw tonnes, spatial donor and
deficit values, the inventory/ledger adjustment, inferred direction, method and
status remain available for audit.

### Coordinate outlier quarantine

Map and reclaim geometry use a single shared coordinate filter. BlendMaster
estimates normal hex-grid spacing from nearest neighbours, builds connected
coordinate components, and quarantines only small components separated from the
main footprint by many normal grid spacings. Larger disjoint components are
retained so a legitimate second footprint lobe is not discarded automatically.

A quarantined or missing coordinate is excluded from the map, automatic axes
and dig path. Its `FINAL_WMT`, grades and properties are not deleted: the hex is
assigned non-spatially to the chunk whose resulting tonnes are closest to the
target chunk size. The UI reports the excluded hex IDs, WMT and allocation
warning. This preserves the authoritative opening balance without inventing a
replacement coordinate.

## Grade-block linkage

Each deduplicated inbound AMT trip is enriched through two paths.

### Exact EXPIT trip match

```text
AMT.INTERNALID = INVENTORY_EXPIT_REHANDLE_TRANSACTIONS.INTERNAL_ID
```

Only non-deleted `PrimaryMovement` rows at or before scenario start are used.
This match supplies `SOURCE_GRADEBLOCK_ID`, `SOURCE_FMS`, feed properties, and
the `PROD1_*` and `PROD2_*` modelled property families. Its match method is
`EXPIT_INTERNAL_ID`.

### AMT truck-list attribute match

The truck-list object does not expose `INTERNALID`, so the candidate join is:

```text
truck.LOCATION_NAME  = inbound.LOCATION_NAME
truck.HEX            = inbound.TARGETHEX
truck.DUMPEDDATETIME = inbound.DUMPEDDATETIME
```

If several candidates exist, BlendMaster prefers a row with a grade-block name,
then the closest `TRUCK_WMT` to authoritative `AMT.TONNES`, then the latest
truck-list update. This path supplies `GRADE_BLOCK`, `ROM_MATS`, `GBI` and the
truck-list chemical properties. When it is the only identity available, its
match method is `TRUCK_LIST_TIME_HEX`.

### Grade-control property match

The resolved EXPIT `SOURCE_FMS` or truck-list `GRADE_BLOCK` is matched,
case-insensitively, to the canonical `FULL_NAME_WITH_SITE` constructed from
`DA_OPERATIONS.STG_GRADECONTROL.GRADE_BLOCKS`. This adds the properties that
are not present as usable AMT hex values: Product 1 minus-1-mm percentage,
fines/lump yields, moisture and assays, grade-block dry density and LOI 425.
EXPIT identity remains preferred for lineage identity; the grade-control join
enriches that lineage and does not create a new movement or tonnes record.

Grade-block identity is resolved in this order:

```text
ID:<SOURCE_GRADEBLOCK_ID>
NAME:<upper-case grade-block name>
UNMATCHED
```

Numeric identity is therefore not replaced by an arbitrary name match. The JSON
lineage retained for each hex includes the identity, name, match method, inbound
WMT, trip count, first/last dump timestamps, `ROM_MATS`, and modelled property
values.

## Proportional depletion and final-tonnage alignment

AMT does not provide explicit grade-block lineage for each reclaim from a mixed
hex. BlendMaster therefore uses a declared **proportional-depletion** assumption.

For grade block `g` in hex `h`:

```text
inbound_share[g,h] = inbound_wmt[g,h]
                   / sum(inbound_wmt[all grade blocks,h])

final_lineage_wmt[g,h] = FINAL_WMT[h] * inbound_share[g,h]
```

This is equivalent to assuming every reclaim removes all contributing grade
blocks in their existing proportions. It also aligns lineage to spatial donor
reductions and the final inventory scaling without changing the assumed
composition. A zero-tonne final hex has zero remaining lineage tonnes.

The method is not FIFO and does not claim to identify the physical layer lifted
by an individual reclaim.

## Modelled hex properties

For every numeric property `p`, BlendMaster calculates an independent non-null
weighted average:

```text
modelled_p[h] = sum(p[g,h] * final_lineage_wmt[g,h]
                    where p[g,h] is not null)
              / sum(final_lineage_wmt[g,h]
                    where p[g,h] is not null)

property_coverage_pct_p[h] = 100
                           * property denominator / FINAL_WMT[h]
```

Independent denominators prevent one missing property from suppressing the
properties that are available. Output fields use the pattern:

```text
MODELLED_<PROPERTY>
MODELLED_<PROPERTY>_COVERAGE_PCT
```

The current property families include:

- truck-list grade-block chemistry and `GBI`;
- EXPIT feed mass recovery, goethite, ultrafines below 1 mm, moisture, LOI and
  ore-type fractions;
- EXPIT `PROD1_*` and `PROD2_*` chemistry, recovery and physical properties;
- grade-control Product 1 minus-1-mm, CB fines/lump yields, moisture and
  assays; and
- dominant `MODELLED_ROM_MATS`, selected by the most remaining lineage tonnes.

Ore-type fractions are also combined into `MODELLED_DOMINANT_ORE_TYPE` by
selecting the category with the largest modelled fraction.

### Additive tonnes and size products

Additive source properties are derived at final-hex mass rather than treated as
weighted-average grades:

```text
feed_wmt = FINAL_WMT
feed_dmt = FINAL_WMT x (1 - feed_moisture)

oretype_<type>_wmt = lineage final WMT x ore-type fraction
oretype_<type>_dmt = lineage feed DMT x ore-type fraction

prod<n>_dmt = lineage feed DMT x PROD<n> mass recovery
prod<n>_wmt = prod<n> DMT / (1 - PROD<n> moisture)

prod<n>_minus_1mm_wmt = prod<n> WMT x minus-1-mm fraction
prod<n>_minus_1mm_dmt = prod<n> DMT x minus-1-mm fraction
```

The ore types are `bid`, `cidl`, `cidm`, `cidu`, `did`, `hc` and `other`.
Minus-1-mm uses `prod<n>_minus1mm_pct` when present and otherwise the EXPIT
`prod<n>_mudrush_ultrafines_1mm` value. Values in either 0..1 or 0..100 form
are normalised to a fraction before multiplication.

For CB Product 1, grade-control fines and lump yields are applied to lineage
feed WMT/DMT. When both size yields are present, the canonical `prod1_wmt` and
`prod1_dmt` equal the conserved sum of the two size products instead of the
EXPIT recovery result. Each `prod1_fines_<assay>` or
`prod1_lump_<assay>` is weighted by its own component mass. Canonical aliases
such as `prod1_minus_1mm_pct`, `prod1_fines_wmt` and `prod1_lump_fe` are shared
with inventory and mapped APS sources; shorter `fines_*` and `lump_*` aliases
are retained for existing projects.

Every additive property still receives its own coverage value. A missing
moisture, yield, recovery or size fraction leaves the dependent tonnes blank
rather than manufacturing zero tonnes.

`MODELLED_PROPERTIES_JSON` retains the complete value and coverage dictionaries.
`GRADE_BLOCK_LINEAGE_JSON` retains the contributing grade-block detail.

The generic truck-list chemistry is lineage audit data. It does **not** replace
the Fe, SiO2, Al2O3, P or Mn insitu grades from `AMT_HEX_GRADES`.

## AMT data-stream semantics

The internal inventory-derived `ROM / insitu` blend factor and `PRODn / ROM`
upgrade factor are no longer part of the AMT grade calculation.

| Stream | AMT meaning |
| --- | --- |
| Insitu | Existing hex Fe, SiO2, Al2O3, P and Mn from `AMT_HEX_GRADES`. |
| Modelled ROM | The unreconciled ROM baseline is the insitu hex vector; no internal inventory blend factor is applied. |
| Adjusted ROM | `insitu grade x historical OPF/brand blend recon`. |
| Modelled Product | Grade-block-lineage-weighted `PROD1_*` or `PROD2_*` analyte vector for the applicable OPF product channel. |
| Adjusted Product | `modelled product x historical OPF/brand regression recon`. |

Product-channel rules remain:

- CB and CC OPF01 use Product1;
- CC OPF02 and VK/KV use Product2;
- EW and FT are dry plants, use the ROM stream, and have regression fixed at
  1.0; and
- IB remains unconfirmed and must fall back with a warning until configured.

The historical factor is resolved independently for each OPF, brand and analyte
using the established shortest-successful-window and fallback rules. Physical
properties such as ultrafines, recovery and ore type remain modelled only;
historical blend/regression factors are not applied to them.

## Audit fields and fallbacks

Important per-hex diagnostics include:

| Field | Meaning |
| --- | --- |
| `LINEAGE_ENTRY_COUNT` | Number of lineage records, including an unmatched record when present. |
| `GRADE_BLOCK_COUNT` | Number of distinct resolved lineage identities, excluding `UNMATCHED`. |
| `LINEAGE_INBOUND_WMT` | Inbound WMT represented in the lineage collection. |
| `LINEAGE_MATCHED_WMT` | Inbound WMT with EXPIT or truck-list attribution. |
| `LINEAGE_UNMATCHED_WMT` | Inbound WMT retained under `UNMATCHED`. |
| `LINEAGE_FINAL_WMT` | Spatially and inventory-reconciled final WMT to which lineage was aligned. |
| `LINEAGE_MATCHED_FINAL_WMT` | Final WMT represented by the matched lineage share. |
| `LINEAGE_UNMATCHED_FINAL_WMT` | Final WMT represented by the unmatched lineage share. |
| `LINEAGE_COVERAGE_PCT` | `100 x matched inbound WMT / lineage inbound WMT`. |
| `LINEAGE_WARNING` | Missing or incomplete lineage warning. |
| `MODELLED_<PROPERTY>_COVERAGE_PCT` | Percentage of final hex tonnes supporting that property. |

Fallback behavior is deliberately conservative:

- unmatched inbound tonnes remain in the lineage and total, but do not invent
  property values;
- a positive `FINAL_WMT` with no lineage produces null modelled properties and
  an explicit warning;
- partial coverage of an active `PROD1_*` or `PROD2_*` optimiser analyte is
  shown explicitly and warned; its modelled grade is calculated only from the
  lineage tonnes carrying that property;
- missing properties fall back independently through the configured grade
  stream rules only when they are optimiser analytes; and
- a historical factor falls back by OPF/brand/analyte as documented in
  [Data Streams](DATA_STREAMS.md), ultimately using 1.0 with a warning when no
  defensible result exists.

## Limitations

- Proportional depletion assumes uniform mixing within a hex. It is not FIFO or
  physical layer tracking.
- An internal hex-to-hex rehandle is fully attributable only when its inbound
  record retains grade-block or product properties. Exact propagation from the
  source hex would require a chronological material ledger.
- Inventory opening/adjustment tonnes and AMT movements without a hex have no
  defensible grade-block identity.
- The truck-list match uses exact location, hex and dump timestamp because no
  common trip ID is exposed. Unmatched lineage and coverage must therefore be
  reviewed before relying on modelled properties.
- `AMT_HEX_GRADES` supplies the existing insitu snapshot; the movement
  reconstruction does not recreate historical insitu chemistry.
- Modelled product fields depend on the relevant EXPIT `PROD1_*` or `PROD2_*`
  value being populated. A blank remains a blank and follows per-analyte
  fallback; it is never interpreted as zero.
