# 00 - BlendMaster Product Scope and Validation Baseline

## Purpose and handover status

BlendMaster is a planning decision-support system for selecting, sequencing and assessing ore blends from ROM inventory, AMT stockpile chunks and scheduled ex-pit deliveries. It balances equipment, source availability, chemistry, product builds, destination capacity and transport timing. It supports a single tipping point, several physical tipping points feeding one OPF, and combined OPFs sharing physical inventory.

This collection is the implementation handover for software engineering, production operations and UI/UX. The current deliverable is a Windows desktop proof of concept used to validate the business system. A production web service has not been deployed or accepted. Calculation completion is separate from target attainment, physical validity, freshness and permission to issue a plan.

**Baseline:** 14 September 2026; branch `main_BlendMaster_ultimate_prod_streams`; committed source `becfca8`, plus the v0.3.326 AMT presentation and release changes described here. Existing technical specifications in the repository remain useful detailed algorithm references; earlier task notes are historical records and can describe superseded behaviour.

## Release identity

The selected validation release is **v0.3.326**. This is a documented development convention in the requested three-number format: 0 denotes pre-production, 3 denotes the next feature generation after the existing v0.2 product-stream application, and 326 records the reachable commit-count baseline. It is not a claim that 326 semantic patch releases occurred.

Git history begins on 20 October 2024. There are 77 commits dated 2024, 52 dated 2025 and 197 dated 2026: 326 total, including merges; first-parent history has 315. There were no release tags. Reproduce with `git rev-list --count becfca8` and `git log --reverse --format="%h %cs %s"`. Do not derive release maturity by splitting the digits of a count. `AppVersion.py` is the release identity source; artwork also embeds the label and needs checking at release time. Adopt API-based [semantic versioning](https://semver.org/) when the production API contract is established.

## Retained validation model

The sole retained project found for this handover is `CC_Combined_OPF_Validation.prj` in `C:\BlendMaster\blendmaster_OOP`. Earlier Planner, shared-publication and original hard-target copies were removed by the owner. Historical test evidence referring to those copies is evidence of the earlier exercise, not a list of files still available.

The retained model is the **soft-target** combined-OPF validation case. Do not describe it as proof that hard production targets are feasible. Record its checksum, planning timestamp, source revisions and exact software revision in every subsequent validation receipt. Keep operational target approval separate from this software handover.

## Capabilities to preserve

| Area | Required behaviour |
| --- | --- |
| Source preparation | Explicit mappings, five grade streams, independent quantity bases, missing-value and coverage evidence |
| AMT | Build-specific opening reconstruction, signed-tonnage guards, spatial reconciliation, lineage and ordered chunks |
| Planning | Calendar limits, direct-tip eligibility, custom constraints, hard/soft quality policy and alternatives |
| Multiple OPFs | Shared physical balances, independent OPF chemistry and point-specific operating limits |
| Transport | Tipping and OPF arrival clocks, conveyor storage, COS FIFO contents and closing balances |
| Continuous operation | Bounded assay updates, source-revision checks, scheduled preparation and selective Planner imports |
| Delivery | Saved result snapshots, manual comparison, per-point plans, auditable PDF/XLSX outputs |

## What validation establishes

The completed September 13 desktop acceptance recorded 636 feed rows across 211 steady states in both optimised and copied manual results, through August 20 at 06:00. Equipment checks passed; the largest reported transport mass-balance error was 6.9e-7 WMT. A separate check matched 2,000 haulage assignments, including 786 destination changes, to the 2WP route evidence. These are historical, bounded tests.

Outstanding operating findings include product-volume shortfalls and soft grade breaches. The historical model took roughly 20 minutes to reopen. Live assay application requires measured per-OPF alignment lags. None of these observations establishes web capacity, multi-user safety, unattended reliability or site-wide production acceptance.

## Reading guide

Start with 01 for user journeys, 04 for the current architecture, 06 for the proposed web architecture and 12 for delivery ownership. Engineers then use 02–03 and 05, 07, 09–11 for contracts. Page 08 defines acceptance gates.

**Evidence:** [completed desktop acceptance](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/docs/CONTINUOUS_OPERATIONS_IMPLEMENTATION.md), [current workflow roles and stages](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/SiteWorkflow.py), [planning modes](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/MultiFeedSettings.py).

