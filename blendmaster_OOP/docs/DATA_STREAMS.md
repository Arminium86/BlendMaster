# Data Streams

BlendMaster retains five grade streams for Fe, SiO2, Al2O3, P and Mn and
projects one selected stream onto the existing optimiser grade fields:

1. Insitu
2. Modelled ROM
3. Adjusted ROM
4. Modelled Product
5. Adjusted Product (default)

Resolution falls back independently per analyte in the order selected stream,
next available upstream stream, then the legacy grade. A warning is retained
when fallback occurs.

## Source calculations

APS grade blocks use the exact brand-specific ROM and product headers mapped on
the Data Streams page. Legacy projects with one all-brand ROM mapping replicate
that mapping across configured brands. APS grades are authoritative, so no OPF
factor is applied.

Inventory stockpiles use:

- Modelled ROM = imported inventory ROM
- Adjusted ROM = inventory ROM x historical blend recon
- Modelled Product = imported inventory product for the OPF product channel
- Adjusted Product = inventory product x historical regression recon

AMT hexagons use:

- Modelled ROM = hex insitu x inventory internal blend recon
- Adjusted ROM = Modelled ROM x historical blend recon
- Modelled Product = Adjusted ROM x inventory internal upgrade
- Adjusted Product = Modelled Product x historical regression recon

The inventory internal blend recon is `ROM / insitu`; internal upgrade is
`PRODn / ROM`. CC OPF02 inventory PROD3 is normalised to BlendMaster Product2.
CB and CC OPF01 use Product1; CC OPF02 and VK/KV use Product2. EW and FT are dry
plants, so product streams alias Adjusted ROM and regression is locked to 1.0.
IB product mapping remains unconfirmed and therefore falls back to Adjusted ROM
with a warning.

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
not raw tonnes, is used for AMT chunking. Grade redistribution is intentionally
outside this tonnage-only stage.

## Historical OPF factors

Only completed shift dates strictly before scenario start are used. Each brand
and analyte retains the shortest successful window in 7, 14, 21, 28, then 30
days. Daily blend factors are weighted by FEED_WMT; daily regression factors are
weighted by PROD_WMT. Missing brand/analyte results use an available OPF brand
when possible, otherwise factor 1.0 with a warning. Calculated and user-edited
effective factors are stored separately.

The product-stream Planning Plan category defaults to `OPF Production`; ROM
streams default to `OPF Feed`. Both remain configurable for APS model variants.

## APS grade-field browser

The Data Streams screen includes the first 24HR `Mining.csv` selector in the
workflow, reads its header row and presents the distinct fields beside the
mapping grid. The selected path is carried forward into Guidance Schedules.
Select a grade mapping cell and either double-click a field or drag it onto
that cell. This avoids transcription errors in long APS process-stream field
names.

## Database View

Stockpile Inventories always proceeds to AMT Stockpiles. Submitting the AMT
step then opens **Database View**. This source-level audit snapshot is reused by
the next optimisation run. It includes:

- selected inventory stockpiles, excluding the duplicate inventory instance
  of a stockpile selected as AMT;
- every selected AMT chunk in reclaim sequence;
- one tonne-weighted row per APS grade-block source whose movement falls inside
  the configured planning horizon; and
- every flattened grade stream plus the selected stream vector and per-analyte
  fallback provenance for each configured brand.

The table intentionally excludes payload-level movement, destination, calendar
and solver-configuration fields. It is a focused audit of the source tonnes and
grades entering scheduling.

### Database View field dictionary

The table is deliberately wide because it shows both the values supplied to
the optimiser and the intermediate values used to derive them. Columns fall
into four groups.

#### Source identity and quantity

| Field | Meaning | When a blank is expected |
| --- | --- | --- |
| `source_type` | `Inventory Stockpile`, `AMT Chunk`, `AMT Stockpile - No Chunks`, or `APS Grade Block`. | Never for a valid row. |
| `source_id` | Inventory stockpile name, AMT chunk/hex identifier, or APS grade-block source name. | Never for a valid row. |
| `parent_stockpile` | The stockpile footprint that owns an inventory row or AMT chunk. | APS grade blocks do not have a parent stockpile. |
| `build_or_chunk` | Inventory build name for an inventory row; chunk/hex identifier for an AMT row. | APS grade blocks and the AMT no-chunks warning row. |
| `sequence` | AMT reclaim sequence number. | Inventory stockpiles and APS grade blocks. |
| `tonnes` | Selected inventory balance, selected AMT chunk balance, or the sum of APS payload tonnes for that grade block inside the planning horizon. | A valid scheduling source should not be blank. The AMT no-chunks warning row intentionally shows zero. |

APS payloads are consolidated into one grade-block row. Its grades are
independently tonne-weighted per analyte, so a missing analyte does not prevent
the available analytes from being shown.

#### Internal AMT reconciliation

| Field | Meaning | When a blank is expected |
| --- | --- | --- |
| `internal_recon_matched` | Whether the AMT footprint was successfully matched to the corresponding inventory stockpile instance used to derive its internal blend and upgrade factors. | All inventory and APS rows. |
| `matched_inventory_stockpile` | Name of the matched inventory stockpile. | All non-AMT rows, or an unmatched AMT row. |
| `matched_inventory_build` | Inventory build selected by the time-based match. | All non-AMT rows, or an unmatched AMT row. |
| `matched_inventory_time` | Transaction timestamp of the matched inventory state. | All non-AMT rows, or an unmatched AMT row. |

These columns are diagnostics, not additional optimiser inputs. For an AMT
row, an unsuccessful or missing match is actionable because its internal
factors may have fallen back to 1.0; the `warnings` column records the applied
fallback.

#### Grade columns

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

Not every raw stream column is used at the same time. BlendMaster retains all
five so the selected result can be traced and the user can switch streams
without losing the underlying calculations. The `selected_...` columns are
therefore derived from the wider `grade_...` set, but are intentionally kept
because they show the exact effective vector after fallback.

#### Warnings and cell colours

| Appearance/field | Meaning | Action |
| --- | --- | --- |
| Pale yellow `fallback_...` cell | A fallback **was used** for that brand/analyte. The cell contains the requested and substituted stream/brand; hover to see the full text. | Review whether the substitution is acceptable. It is not itself a missing required cell. |
| Blank `fallback_...` cell with the normal background | No fallback was required. | None; this is the preferred state. |
| Blank pale red/pink `grade_...` or `selected_...` cell | No value is stored/resolved for that exact field. | Check it when it belongs to the selected stream and active brand. It can be expected for streams or brands that do not apply to the source. |
| `warnings` | Consolidated source-calculation, reconciliation and per-analyte fallback messages for the row. | Review any message that affects the active brand or selected stream. |

The UI does not colour an empty fallback cell yellow. A yellow cell always has
fallback provenance, although its text can be easier to read from the tooltip.

### Which blanks matter?

Only a small subset of the wide row is essential to one scheduling decision:
the source identity, available tonnes, and the five `selected_<active
brand>_<analyte>` values. A blank selected grade is not necessarily fatal
because individual constraints may not use every analyte, but it must not be
silently interpreted as a grade of zero.

The remaining blanks fall into these expected categories:

- source-specific structure: APS rows have no stockpile/build/reconciliation
  fields, and inventory rows have no AMT sequence or match fields;
- unused streams: dry plants, APS mappings, or a particular source may not
  carry every possible intermediate stream/brand combination;
- audit-only provenance: blank `fallback_...` fields mean no fallback occurred;
  and
- unavailable data: pale red/pink grade cells and warnings identify cases that
  need review when they intersect the selected stream and active brand.

Some columns are redundant for solving but not for diagnosis. Raw grades,
intermediate streams, match metadata, fallback provenance and warnings can be
ignored by the optimiser, yet they explain exactly how the effective selected
grades were produced. They should be retained in Database View while the new
data-stream calculations are being validated.

## Audit and reporting

Inventory and AMT setup views show the calculated streams for every configured
brand. AMT rows also retain whether an inventory instance was matched, the
matched stockpile/build/timestamp, the match rule, and the internal blend and
upgrade factors used for the calculation.

Optimised and manual blend reports retain the selected stream and brand, the
five grades actually used by the solver, and all five analytes for every raw
stream as `source_grade_<stream>_<analyte>`. The Optimised Blend Sequence
transaction table exposes the same fields, allowing a reported decision to be
traced back through adjusted product, modelled product, adjusted ROM, modelled
ROM and insitu values without re-running the model.
