# Task 5: Advanced reconciliation history service

Implemented 4 September 2026 on `main_BlendMaster_ultimate_prod_streams`.

## Delivered

`setup/ReconciliationHistory.py` exposes historical blend and regression factors
with their contributing parent-grade-block identities and feed tonnes. It uses
the versioned `PhaseSchemas.reconciliation_sample` record, extended compatibly
with an optional `provenance` mapping.

`setup/sql/opf_shift_reconciliation.sql` retains the existing factor formulas
while grouping feed, product assays and tails by OPF, SHIFT_DATE and SHIFT.
Snowflake metadata and live records confirmed `Day` and `Night` shift fields on
all three source views. Periods follow the existing 06:00/18:00 Perth calendar.
Only complete shifts wholly inside the requested lookback are returned.

Each OPF/brand/shift has two samples, one per factor kind. Each carries all five
analytes, total period feed WMT, product DMT, feed row count, period timestamps,
contributing blocks and audit provenance. The factor is not recomputed from a
spatial subset or duplicated once per contributing block. Both advanced factor
kinds retain **total period feed** as their cross-period aggregation weight.

Standard `DataStreamReconciliation.fetch`, its daily SQL, existing weighting,
fallbacks and Cloudbreak campaign behavior remain unchanged. The new service is
called explicitly through `DataStreamReconciliation.fetch_history`.

## Inventory lineage and attribution limits

Live OPF-feed rows identify stockpile builds, such as a footprint with a trailing
build number, rather than the original grade blocks. The service links those
exact build identities to inbound `DESTINATION` movements using the confirmed
`WMT_REPORTING` field. The new `inventory_build_lineage.sql` reads selected builds
in batches of 100 and only through the scenario timestamp. A historical sample
only uses inbound history available by that sample's period end.

`setup/InventoryBuildLineage.py` constructs proportional build composition from
the inbound ledger. Its basis is explicitly recorded as
`proportional_exact_build_inbound`. This is inferred bulk composition: selective
reclamation within a build is not measured by this service. A shift's feed is
allocated using cumulative composition through the end of that shift.

- Different builds of the same footprint remain separate.
- Grade-block aliases and signed corrections are combined before weighting.
- Negative net or invalid lineage is marked unavailable.
- Unknown inbound sources retain their share of feed. Known blocks are never
  renormalized to imply complete coverage.
- Missing/invalid factors stay `None`; the service does not silently set them
  to 1.0. Missing advanced history is reported for downstream standard fallback.
- Original warehouse brands are retained. Unique suffix aliases such as
  `SF -> CBSF` can select history; ambiguous aliases are reported.
- Provenance includes attributed/unattributed WMT, lineage coverage, shift and
  sample identity, product WMT, CBFL campaign marker and quality warnings.

**Lineage coverage is not a grade-prediction confidence score.** Spatial and
compositional matching and confidence calculation belong to Task 6.

The separate `fetch_inventory_lineage` entry point accepts exact opening-inventory
`build` values. It scales known composition to the latest as-of inventory balance,
retains unknown tonnes, and does not inflate non-positive or unavailable balances.

## Validation completed

- 26 new offline tests passed.
- Full suite: **501 tests passed**; the two existing pandas warnings remain.
- Live Cloudbreak check, two days ending 22 August 2026 at 06:00 Perth:
  16 samples, four shifts, CBSF and CBFL, about 11 seconds; attributed feed
  coverage 99.59–100%. The unmatched feed remains explicitly unknown.
- Live default 30-day check at the same cutoff: 120 CBSF samples across
  60 shifts, about 15 seconds. Thirty-six samples report incomplete lineage;
  they retain the missing share and warning rather than claiming full coverage.
- Live opening-inventory check: two positive-balance builds, 178 contributing
  blocks, no missing-lineage warnings for those selected builds.
- Controlled Snowflake SELECT-only fixture: independently checked Day/Night
  separation, two-brand tails allocation, total feed weights, blend/regression
  formulas and absence of row multiplication.
- Live sample and inventory records serialize as strict JSON without NaN or
  Infinity. No warehouse or application database records were written.

## User validation

From `C:\BlendMaster\blendmaster_OOP`:

```powershell
python -m unittest tests.test_reconciliation_history -v
python -m unittest discover -s tests
```

For a live check in Python from that directory:

```python
from setup.DataStreamReconciliation import DataStreamReconciliation

samples, warnings = DataStreamReconciliation().fetch_history(
    "2026-08-22 06:00:00", "CB OPF", ["SF"], max_lookback_days=30
)
print(len(samples), len(warnings))
print(samples[0]["provenance"])
```

Inspect `contributing_blocks`, `feed_wmt`, `factors` and `provenance` for a familiar
period/build. The history service is read-only and applies a 60-second timeout
per warehouse statement. Exact-build inbound history covers the build lifetime,
which can predate the factor lookback; later movements are excluded.

There is no new UI in Task 5. Controls and review UI are Task 8, factor application
Task 7, and persistence/cache migration Task 34. The current standard calculation
and solver continue to run as before. Task 6 has not started.
