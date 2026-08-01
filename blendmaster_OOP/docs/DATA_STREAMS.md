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

The Data Streams screen reads the header row from the selected 24HR
`Mining.csv` and presents the distinct fields beside the mapping grid. Select
a grade mapping cell and either double-click a field or drag it onto that cell.
This avoids transcription errors in long APS process-stream field names.

## Database View

After selected AMT chunks (or inventory-only stockpiles) are submitted, Setup
opens **Database View**. This source-level audit snapshot is reused by the next
optimisation run. It includes:

- selected inventory stockpiles, excluding the duplicate inventory instance
  of a stockpile selected as AMT;
- every selected AMT chunk in reclaim sequence;
- APS payloads whose delivery timestamp falls inside the configured planning
  horizon;
- opening, incoming and projected tonnes, calendar state, reclaim threshold,
  modelled Auto-turnover time and scenario-start availability;
- every flattened grade stream plus the effective optimiser vector and
  per-analyte fallback provenance for each configured brand.

APS incoming tonnes outside the horizon are shown separately because the
current stockpile Auto-turnover calculation observes the complete prepared
payload population. This makes a stockpile withheld by later APS deliveries
visible rather than presenting it as an unexplained missing option.

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
