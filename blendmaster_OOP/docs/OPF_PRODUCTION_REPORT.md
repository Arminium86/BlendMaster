# OPF Production Report

The report is in **Workspace**, immediately after **Product Targets**. It is
read-only and uses the product-assay source shared with reconciliation.

## UI review guide

1. Open **Workspace → OPF Production Report** after loading your scenario.
   **From (AWST)** and **To (AWST)** initially cover the 24 hours before the
   scenario start. **Last 24 hours** restores that window.
2. Select **OPF** and **Product brand**, then **Refresh**. Changing OPF or dates
   clears the old scope and requires Refresh. Brand and aggregation changes
   redraw the loaded records immediately.
3. Compare **Raw observations**, **Shift (DMT weighted)** and **Daily (DMT
   weighted)** for Fe, SiO2, Al2O3, P and Mn. Hover points for values, DMT,
   assayed-DMT coverage and sample time. Edge periods may be partial.
4. Compare **LQL**, **Target** and **HQL** with Product Targets. Hover vertical
   build-change markers and expand **Target build details** to see all visible
   intervals and unrounded specifications. Dated targets apply only over their
   known intervals. The first undated build is labelled a current reference.
   A historical window may precede the dated 2WP targets; this is reported.
5. With multiple OPFs configured across site scenarios, choose **All configured
   OPFs** and toggle **Show combined series**. Per-OPF values remain visible.
   Choose a common brand such as SF to compare warehouse aliases. The combined
   value uses actual DMT; missing OPF contributions are explicitly noted.
   Quality specifications remain attached to their own OPF.
6. Set **Auto-refresh (minutes)** or choose **Off**. The timer pauses when the
   tab is hidden and keeps the selected window fixed. Report filters and the
   interval persist per scenario and in the project.

## Data, aggregation and cache

`setup/ProductAssayHistory.py` reads
`AA_OPERATIONS_MANAGEMENT.SELFSERVICE.INVENTORY_OPF_PRODUCT` using a bounded,
parameterized query. Production is placed at `TRANSACTION_DATETIME` converted
to AWST. Assay sample and warehouse update times remain separate provenance;
sample time is often absent. Naive timestamps follow the app's AWST convention.
The start is inclusive and the end exclusive. This is the warehouse's currently
available history, not a reconstruction of what was known at a past scenario start.

Raw observations retain each production record and associated assay. Shift
averages use source shift assignments (06:00/18:00); daily averages use AWST
calendar days. Each analyte uses its own valid-assay, positive-DMT denominator.
Missing assays stay missing and genuine zero stays zero. Combined values use
constituent raw DMT, never equal OPF weights. At raw grain, combined records
share a transaction time.

OPFs use the existing canonical names. Brands resolve to an exact warehouse
product or a unique suffix match per OPF; ambiguous aliases are reported.
Queries allow up to 93 days and 200,000 records, with a 60-second warehouse
statement timeout. Larger results require a narrower selection.

The persistent cache keeps 24 recent entries under
`%LOCALAPPDATA%/BlendMaster/product_assay_history`. A five-minute fresh cache and
offline fallback match the exact source, query version, window and OPFs. Refresh
forces a warehouse read; an empty successful response replaces previous data.
Cached fetch and update timestamps are retained during an outage.

| State | Expected UI behavior |
|---|---|
| Loading | Inline progress and disabled Refresh; previous data may remain for the same selection |
| Cached | Explicit cached-data label and original fetch timestamp |
| Snowflake unavailable, matching cache exists | Amber message and matching cached charts |
| Snowflake unavailable, no matching cache | Explicit unavailable message and no curve from a different selection |
| Successful empty response | No-product-record status and no assay curves; target references may remain |
| Records exist but assays are absent | Missing values and an explicit no-valid-assay warning |

## Implementation and validation

`GUI/OPFProductionReport.py` provides native Qt controls and Matplotlib charts.
The existing background-task runner performs reads; request generation tokens
prevent late replies from replacing a newer scenario/window. The tab is available
before optimisation inputs are complete once an OPF and start time exist.

`classes/ProductAssayReport.py` resolves dated, row-owned specifications, keeps
CB lump/fines separate and reports ambiguous aliases or overlapping ownership.
Legacy Min/Max do not imply a central Target; combined quality limits are not
invented. The report does not change reconciliation or solver enforcement.

Matplotlib is declared in requirements and the SQL is included in both executable
build specifications. A packaged application requires rebuilding after changes.

Run focused checks from `blendmaster_OOP`:

```powershell
python -m unittest tests.test_product_assay_history tests.test_opf_production_report -q
```

Generate illustrative UI captures locally:

```powershell
python -m tests.render_opf_production_report
```

The renderer writes `docs/screenshots/task15/`, which is intentionally ignored
by Git. Captures cover loading, loaded, raw, daily, cached, offline-cache, no-data
and compact layouts. They use synthetic fixtures and do not query the warehouse.
The native Windows application can also be checked through its normal navigation
and background worker; its existing QtWebEngine views require the Windows
platform rather than the offscreen platform used for report component captures.
