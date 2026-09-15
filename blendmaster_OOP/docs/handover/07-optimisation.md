# 07 - Optimisation and Scheduling Behaviour

## Planning algorithm

BlendMaster selects feasible source quantities over successive steady states. A state ends when relevant conditions change: period boundary, source depletion, build completion, availability or transport/COS conditions. It then updates physical material and build state before making the next decision.

Single-point planning uses the existing CaseModeller/EventPool/Optimizer path. Multi-point and combined-OPF planning use joint allocations so all points compete against one physical stock balance. This sequential planning model must not be described as a guaranteed globally optimal full-horizon schedule.

## Objective and constraints

The optimiser minimizes source/haulage costs and enabled penalties, less throughput and enabled rewards. The default throughput incentive strongly favours feasible throughput. Other terms rank guidance, continuity, direct-tip preference, source choice and stockpile dynamics. A reward does not override a hard constraint.

| Rule family | Behaviour to preserve |
| --- | --- |
| Physical supply | Non-negative quantities, available balance, source eligibility and timing |
| Equipment | Calendar total crusher capacity and per-feed-source reclaim limits |
| Direct tip | Enabled routes/equipment, payload availability and ratio bounds |
| Calendar | Period-specific rates, source states, allowances and custom limits |
| Chemistry | Exact configured grade stream and declared mass weights |
| Builds | Cumulative mass/metal, opening inventory, target policy and build progression |
| Source selection | Configured stockpile counts, contribution, pair/continuity and duration rules |
| Custom properties | Explicit mapped coefficients and valid aggregation semantics |
| Shared operation | One stock balance across points and independent OPF chemistry |

Grade policy is explicit. Hard limits constrain feasibility; soft product targets permit deviations with scoring and review evidence. Where the configured policy permits intermediate off-spec feed while requiring a compliant ultimate build, preserve cumulative completion/repair logic. Never relax constraints silently to make a run finish.

## Custom constraints

Evaluate canonical source expressions according to declared property kind. Additive quantities sum over the selected source fractions. Weighted averages use the declared additive denominator. Literal constants are scalars.

Examples: total component WMT / total product WMT is an additive ratio; a grade / 1 is a weighted average, not a sum of source grades. Missing, non-numeric or non-finite referenced fields should fail preparation with the constraint and source identified. Reject unsupported nonlinear combinations instead of approximating them invisibly.

A property shown in a field selector is not proof of complete source coverage. Display partial coverage before a planner attempts to constrain it.

## Physical evolution and alternatives

After accepting a decision, deplete sources once, apply non-direct-tip destination allocations, advance transport and accumulate product on the correct time/mass basis. Preserve the immutable checkpoint used for repair or alternative cases. Plan identity scopes reports, destination ledgers, backups and transport contents.

Contingencies are named result alternatives. Select the best accepted result using existing ranking, but retain its status and limitations. A feasible state or an incumbent CBC solution does not establish target attainment for the full planning horizon.

## Manual equivalence boundaries

Manual single-point recipes use the same canonical quantities, grade weights, physical balances and Calendar equipment limits. Ratio rounding triggers recalculation; changed ratios cannot be applied only to displayed percentages. Direct-tip allocations remain constrained by their arrival availability.

Combined-mode manual handover copies the selected current joint allocations and related result state into an independent named manual result. It does not independently solve each point or provide an implemented joint manual editor. Engineering should agree any future editing scope as a separate feature.

## Diagnostics and service extraction

Expose structured failure categories for missing input, infeasibility, time/resource limits, cancellation and rejected post-solve checks. Save solver configuration, logs/status, relevant input revision and candidate diagnostics. Keep previous accepted results available when a new run fails.

Use deterministic frozen fixtures to compare the extracted web worker with the desktop. Compare physical conservation, constraint outcomes, grade weights, timing, build progression and readiness. Exact alternative-source selection may differ where multiple equivalent optima exist; acceptance tolerances must be explicit.

**Evidence:** [single-point engine](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/Optimizer.py), [state orchestration](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/CaseModeller.py), [joint optimiser](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/MultiLaneOptimizer.py), [custom expressions](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/CustomConstraints.py), [manual rules](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/classes/ManualBlendRules.py), [detailed objective reference](https://github.com/Arminium86/BlendMaster/blob/becfca8c84f3a5098fdf89d3880b687d80cde641/blendmaster_OOP/docs/OPTIMISATION_OBJECTIVE_AND_CONSTRAINTS.md).

