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

APS grade blocks use the exact ROM and brand-specific product headers mapped on
the Data Streams page. APS grades are authoritative, so no OPF factor is
applied.

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

The product-stream Planning Plan category is intentionally configurable and has
no default until its authoritative value is confirmed. ROM streams continue to
default to `OPF Feed`.
