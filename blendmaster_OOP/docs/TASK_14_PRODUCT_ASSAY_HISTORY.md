# Task 14 — Product assay history service

Completed 7 September 2026. `setup/ProductAssayHistory.py` reads the same
`AA_OPERATIONS_MANAGEMENT.SELFSERVICE.INVENTORY_OPF_PRODUCT` table used by
reconciliation, through a separate parameterized, read-only history query.

- Production records are placed at `TRANSACTION_DATETIME`, converted to AWST.
  `SAMPLED_AT_DATETIME` is often absent and remains separate provenance. Source
  shift assignment is retained; timestamps without a timezone follow the app's
  AWST convention. The window includes its start and excludes its end.
- Raw observations retain each production record and its associated assay.
  Shift averages use source shift date/Day/Night (06:00/18:00 boundaries); daily
  averages use AWST calendar days. Each analyte uses its own valid-assay,
  positive-DMT denominator. Missing values stay missing; genuine zero stays zero.
  Edge buckets contain only records within the selected window.
- OPFs use existing canonical names. A brand resolves to an exact warehouse
  product or a unique suffix match per OPF; ambiguous aliases are reported.
  Only Fe, SiO2, Al2O3, P and Mn are exposed. Combined series are weighted from
  raw DMT, retaining contributing OPFs and assay coverage, never averages of
  OPF averages. At raw grain, combined observations share a transaction time.
- Queries are limited to 93 days, 200,000 records and a 60-second warehouse
  statement timeout. Larger results request a narrower selection; no truncation
  is passed off as a complete result. Connections and cursors always close.
- A five-minute fresh cache and explicit offline fallback use the exact source,
  query version, window and OPFs. The persistent cache keeps 24 recent entries
  under `%LOCALAPPDATA%/BlendMaster/product_assay_history`. Force refresh queries
  Snowflake; an empty successful response replaces previous data. Cached fetch
  and warehouse-update timestamps are preserved on an outage.

The history is the warehouse's currently available view of those production
times, not a reconstruction of what was known at a historical scenario start.
Neither existing reconciliation factors nor solver behavior changes.

Validation: 15 focused automated tests cover timestamp boundaries, weighting,
aliases, bounded parameterized reads, resource cleanup, cache isolation,
corruption and outages. A live CB OPF query for 6 September 2026 06:00 through
7 September 2026 06:00 AWST returned 576 product records: 288 CBSF and 288 CBFL.
No warehouse records were changed.
