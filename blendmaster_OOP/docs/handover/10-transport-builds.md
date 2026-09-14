# 10 - Multi OPF Transport and Product Builds

## Supported topologies

Single-point mode has one physical tipping point. Multi-tipping-point mode feeds one OPF through several points. Combined-OPF mode includes several OPFs and physical points. Configure real crushers rather than treating a Total Feed display aggregate as a physical asset.

A physical source retains one balance across all points. Permitted routes and Calendar operating limits determine where it can feed. Each OPF owns its independent chemistry and target context. A shared product build is an explicit configuration, not an automatic consequence of selecting several OPFs.

## Two operational clocks

Source stock is depleted when material tips. Product targets accumulate when material reaches the OPF. With transport disabled these may coincide; with conveyor/COS enabled they can differ across steady states and Calendar periods.

| Stage | State |
| --- | --- |
| Source/reclaim | Physical WMT, canonical properties and source lineage |
| Tipping point | Direct-tip/rehandle arrivals and Calendar rate/service limits |
| Conveyor | In-transit material and remaining transport capacity/timing |
| COS | Filling/ready FIFO chunks, composition and remaining WMT |
| OPF arrival | Delivered WMT, product quantities and grade-metal contribution |
| Product build | Opening plus received quantities, grades and target progress |

Configured conveyor and COS capacities are ROM WMT. Rehandle service uses payload mass and spot/dump duration; it must respect both service and Calendar capacities. Conveyor payload intervals do not create arbitrary additional solver steady states; material/COS transitions can.

## Opening contents and closing horizon

Use actual opening movements and validated model chemistry. The desktop reconstructs the opening window from configured conveyor/COS capacity and a reference operating Calendar rate. It is an approximation with visible evidence coverage, not a reconstructed historical rate calendar.

Missing opening evidence leaves unobserved capacity empty with a warning; do not invent feed. Missing required chemistry can prevent preparation. Opening context changes invalidate prior evidence.

A stopped crusher pauses its flow according to the current engine and resumes with later operating conditions. Stop at the planning horizon; do not extend a plan just to drain conveyor/COS. Report closing material and its composition.

Illustrative regression case: 600 WMT tipped, 450 WMT arrived, 100 WMT closing conveyor and 50 WMT closing COS conserves physical mass. This example has zero opening transport; with opening material, include it on the input side.

## Product quantity and quality

Product builds use the configured additive product quantity field. Each grade uses its declared weight field, often product DMT. Keep opening build mass and metal in cumulative calculations. Report target, achieved and remaining quantities, steady-state quality and cumulative-build quality.

Hard and soft targets are explicit policies. A soft breach remains visible with its deviation and penalty. Intermediate off-spec allowance and terminal build checks follow configured rules. Do not overwrite imported target values when display numbering changes; identity includes OPF, crusher/brand and actual build period.

Cloudbreak lump/fines mode uses independent product lanes and configured quantity/grade fields. Completing one lane advances that lane while the other retains its current build and balance. Preserve mapped mass and metal balance; reject invalid back-calculated chemistry.

## Operational views

Provide a topology view, time slider, point/OPF rates, active routes, arrival profiles and a COS composition view from the same saved plan. Store diagram coordinates separately from solver settings. Moving a node must not alter physical routes or invalidate a plan.

Blend Plans and PDF exports are point-specific; a workbook can cover all points with common audit sheets. Optimised/manual and named plan selections must resolve all related tables consistently. Avoid combining one plan's feed report with another's transport contents.

## Handover tests

Cover shared-source exhaustion, independent OPF factors, forbidden routes, stops/rate changes, fixed opening feed, late direct tip, partial horizon, product channel mapping, shared versus independent builds, missing opening evidence, mixed FIFO composition and copy/save/restore.

Compare per-point and aggregate conservation plus grade-metal and product-mass calculations. Do not independently replay point reports as if they owned separate stockpiles.

**Evidence:** [topology settings](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/MultiFeedSettings.py), [conveyor/COS engine](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/ConveyorCOS.py), [transport planning](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/TransportPlanning.py), [product lanes](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/ProductBuildLanes.py), [operational plans](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/OperationalBlendPlans.py), [transport acceptance reference](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/docs/TASK_30_35_UI_REVIEW.md).

