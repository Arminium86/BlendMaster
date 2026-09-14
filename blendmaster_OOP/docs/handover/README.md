# BlendMaster — Validation and Production Engineering Handover

Release **v0.3.326** · Updated 14 September 2026 · Desktop validation baseline and proposed web implementation.

BlendMaster prepares and assesses ore-blending plans across inventory stockpiles, AMT chunks, scheduled ex-pit deliveries, physical tipping points and OPFs. This collection is the handover for software engineering, UI/UX, platform and production teams.

The current application is a Windows/Python desktop PoC. The web architecture is proposed; Nova hosting and integration must be confirmed with its owner. Calculation completion does not mean product targets were attained or a plan was approved for production.

## Start here

- Product and production owners: pages 00, 08 and 12.
- UI/UX: pages 01, 05, 10 and 11.
- Software engineers: pages 02–07 and 09–12.
- Platform/Nova team: pages 04, 06, 08, 11 and 12.

## Documentation index

- [00 - BlendMaster Product Scope and Validation Baseline](https://systemsoutline.fmgl.com.au/doc/00-blendmaster-ultimate-product-streams-technical-overview-AumVOmOR3D)
- [01 - User Journeys and UI UX Handover](https://systemsoutline.fmgl.com.au/doc/01-end-to-end-workflow-and-runtime-architecture-N9YKpb5utF)
- [02 - Domain Model and Data Contracts](https://systemsoutline.fmgl.com.au/doc/02-data-streams-canonical-fields-and-setup-workflow-nSUPqaNDg3)
- [03 - Grade Streams and Reconciliation Design](https://systemsoutline.fmgl.com.au/doc/03-data-streams-source-calculations-factors-and-field-mappings-x7yyFRaOOK)
- [04 - Current Desktop Architecture and Extraction Boundaries](https://systemsoutline.fmgl.com.au/doc/04-database-view-data-stream-audit-and-reporting-ywsqE5qGhO)
- [05 - AMT Stockpile Reconstruction and Chunking](https://systemsoutline.fmgl.com.au/doc/05-amt-opening-hexes-and-spatial-reconciliation-87jEbL15rj)
- [06 - Proposed Web Architecture and Nova Integration](https://systemsoutline.fmgl.com.au/doc/06-amt-grade-block-lineage-modelled-properties-and-chunk-semantics-2f8ZAec0J2)
- [07 - Optimisation and Scheduling Behaviour](https://systemsoutline.fmgl.com.au/doc/07-optimisation-objective-source-eligibility-and-custom-constraints-wd7IbZfPQW)
- [08 - Validation Strategy and Production Acceptance](https://systemsoutline.fmgl.com.au/doc/08-optimisation-hard-constraints-guardrails-and-diagnostics-cJ9Tl2RaOz)
- [09 - Input Operations Destinations and Haulage](https://systemsoutline.fmgl.com.au/doc/09-aps-2wp-guidance-turnover-audit-and-material-destination-plan-t62ZVkRmtg)
- [10 - Multi OPF Transport and Product Builds](https://systemsoutline.fmgl.com.au/doc/10-calendar-product-builds-and-closing-rom-compliance-Hu1xnmdPbu)
- [11 - Service APIs Persistence and Reporting Contracts](https://systemsoutline.fmgl.com.au/doc/11-results-reports-sqlite-outputs-and-visuals-fIcUNtqf1E)
- [12 - Engineering Delivery Plan and Operational Handover](https://systemsoutline.fmgl.com.au/doc/12-manual-blend-planning-agent-operations-and-technical-troubleshooting-1g9qkLQz7s)

## Current evidence and limits

- Baseline commit becfca8; 326 reachable commits since 20 October 2024. The release convention is documented on page 00.
- The v0.3.326 regression run passed all 1,269 tests in 50.175 seconds.
- The sole retained local project is CC_Combined_OPF_Validation.prj. It is a soft-target combined-OPF validation case; earlier project copies were removed.
- Historical native acceptance completed 636 feed rows and 211 steady states in optimised and manual results. Product-volume shortfalls and soft grade breaches remain review findings.
- Measured OPF feed-to-assay lags are required for live assay application. Historical reopening took roughly 20 minutes.
- Production hosting, authentication, concurrency, recovery and operational acceptance remain engineering delivery work.

## Maintaining this handover

Repository documentation under docs/handover is the editable source for this collection. Record implementation status and evidence when changing a contract. Preserve the distinction between current desktop behaviour, proposed web interfaces and approved production decisions. Historical task notes may describe superseded behaviour; use current source code and the latest validation receipt when resolving differences.

