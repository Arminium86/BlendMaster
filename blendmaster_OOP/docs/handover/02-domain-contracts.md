# 02 - Domain Model and Data Contracts

## Core model

Separate physical material from its projected chemistry, planning decisions and result presentation. A source can have several OPF/brand chemistry profiles while retaining one physical ROM balance. A source displayed twice must not become two inventories.

| Entity | Identity and grain | Important relationships |
| --- | --- | --- |
| Site model | Stable site/scenario ID plus configuration revision | Owns timezone, OPFs, physical points and policies |
| Input snapshot | Source type, site, as-of time, source revision and schema | Immutable evidence for preparation |
| Inventory build | Site, stockpile and build identity at opening time | Opening WMT and source properties |
| AMT hex / chunk | Footprint/build plus hex or ordered chunk identity | Lineage and member-hex contributions |
| APS payload | Persistent payload/slice identity and source | Loading, route, destination, arrival and WMT |
| Grade profile | Source, OPF, brand, stream and analyte | Value, mass basis, coverage and adjustments |
| Product build | OPF, product lane/brand and build period/identity | Opening mass, target mass and quality policy |
| Plan / run | Site, plan type, named plan, run and input revision | Decisions, reports, warnings and transport state |
| Actual assay | OPF, brand, laboratory sample/window and revision | Physical contribution evidence and estimator update |

Retain raw source identity alongside canonical keys. Normalize grade-block numeric tokens consistently and collapse trailing APS slice suffixes only for parent-level reporting and rules. Do not erase payload identity used for timing and depletion.

## Canonical property contract

Define Fields owns each property's canonical name, kind, weight field and optimisation participation. Map Fields owns explicit raw-to-canonical mappings for Inventory, AMT and APS, including applicable brands. Raw columns with matching names do not bypass an empty mapping.

**Additive** fields represent totals: ROM WMT/DMT, product WMT/DMT and component tonnes. For a selected physical fraction f, consume f times the additive balance.

**Weighted-average** fields represent grades or intensive properties. Their declared additive weight controls aggregation: G = sum(Gᵢ × Wᵢ) / sum(Wᵢ), over valid grade/weight pairs. Preserve denominator coverage. A zero denominator is unavailable, not a zero grade.

For example, 60% Fe on 80 product DMT and 58% on 20 product DMT gives 59.6% Fe. Changing the crusher throughput basis does not change this denominator.

The default five analytes are Fe, SiO2, Al2O3, P and Mn; canonical suffixes are `fe, si, al, p, mn`. The five grade families are `insitu, modelled_rom, adjusted_rom, modelled_product, adjusted_product`.

## Independent mass bases

| Decision | Basis |
| --- | --- |
| Physical stock depletion and conservation | ROM WMT |
| Crusher capacity | Selected additive crusher quantity field |
| Reclaim capacity/reporting | Selected additive reclaimer quantity field |
| Product-build accumulation | Selected additive product-build quantity field |
| Each grade/custom intensive field | Its declared additive weight field |

Missing mapped product mass cannot be silently replaced by ROM mass. Preserve null, zero and unavailable as different states. Validate finite numeric values and units before solver coefficient construction. Exact selected-stream requirements for scheduling are stricter than audit-only display fallbacks.

## Time, provenance and change

Freeze the scenario start, timezone, input revisions, mappings, selected equipment, target policies and solver configuration for a run. Source event time, warehouse ingestion time, assay availability time and plan calculation time are distinct. Store timezone-aware service timestamps and display site-local time; migrate legacy naive local timestamps explicitly.

Every derived output should identify the source revisions and transformation/algorithm version. A prepared snapshot and a published plan have separate lifecycles. Presentation choices such as selected fields or diagram coordinates must not invalidate calculations. Changes to physical inputs, grades, mappings, target policy or relevant recipe inputs must.

## Proposed web schema boundary

Use explicit JSON schemas for commands/configuration and typed table formats for large source and result datasets. Define units, nullability, enum values, identity scope and schema compatibility for every field. Version source adapters independently from planning semantics. Require bounded upload sizes and reject unsupported future schemas before changing active data.

The current Python dictionaries, dynamic report columns and project pickle are migration inputs, not a browser API. Catalogue dynamic properties in a schema registry so the UI can render them without inferring types from column names. Keep a deterministic fixture covering partial depletion, mixed source families, absent mappings and multiple OPFs.

**Evidence:** [field definitions](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/FieldDefinitions.py), [property mappings](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/SourcePropertyMappings.py), [grade streams](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/GradeStreams.py), [identity](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/GradeBlockIdentity.py), [persistence versions](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/PlanningPersistence.py).

