# Task 4: One-lane material-flow topology

Implemented 4 September 2026 on `main_BlendMaster_ultimate_prod_streams`.

## Runtime access

`classes/MaterialFlowTopology.py` adapts existing planning inputs to the versioned
`PhaseSchemas.material_flow_topology` format. Both `CaseModeller` and
`ManualBlendPlanner` expose it through the read-only `material_flow_topology`
property. `execute/Run.py` passes the selected site context into every primary
and contingency case.

Each access returns a detached, derived snapshot. Changing a snapshot cannot
change solver inputs, source balances, build settings or a later snapshot.
No graph is constructed inside the solver loop, persisted or cached. Saved
project migration remains Task 34.

## Graph interpretation

Every configured feed source connects through:

```text
Stockpile / AMT footprint / eligible APS slice
    -> tipping point -> conveyor -> COS -> OPF -> product-build lane
```

- Normal operation has one product lane. Existing byproduct mode has lump and
  fines lanes on the same OPF and tipping point.
- `build_indices` refer to the owning planner's build settings in their existing
  order. They do not introduce a new build sequence or change target values.
- The tipping point retains a copy of the planner's period targets. OPF nodes
  retain period brand ownership.
- Conveyor and COS nodes are explicitly marked placeholders. All edges have
  zero delay and unspecified capacity/rate. They introduce no physical inventory
  or rate constraints. Original crusher rates retain their selected quantity
  basis in period targets; they are not relabelled as physical WMT edge limits.
- `Total_Feed` remains one explicitly marked synthetic tipping point. Physical
  tipping-point expansion is Task 28, combined OPFs Task 29, and latency Task 30.
- Edges represent candidate feed routes, not selected movements, quantities,
  availability windows or destination assignments. Those remain planner state.

Node IDs include site identity, node role and source/lane identity. Escaping
separators prevents ambiguous names; reordering sources or using another plan ID
does not change graph identity. An unnamed site is explicitly unspecified.

Stockpiles and grade blocks with the same name are distinct. Repeated payloads
from one APS slice share a source node and retain their payload IDs. Different
APS slices remain distinct. Manual AMT source nodes retain their chunk IDs;
optimized AMT source nodes refer to the model's stockpile object. No grade-block
parser or AMT lineage key is changed.

## Validation

- 13 new tests cover connectivity, identity, source membership, byproduct build
  order, zero-delay links, legacy callers, serialization and detached snapshots.
- A three-hour constrained solve consumes 300 tonnes at 58% Fe, allocating
  150 tonnes to each of two sequential builds. Reading and modifying the topology
  snapshot does not affect its results, build states or finish time.
- A direct comparison with the pre-Task-4 classes from Git HEAD found identical
  optimized result/build frames, runtime build states and completion time, and
  identical manual steady states and report frames for the same fixture.
- Full unittest suite: 470 passed. The two existing pandas warnings remain.

Run from `C:\BlendMaster\blendmaster_OOP`:

```powershell
python -m unittest tests.test_material_flow_topology -v
python -m unittest discover -s tests
```

## User validation

Restart BlendMaster to load the changed Python modules. Re-run a familiar
single-crusher case, then check source tonnes/grades, steady-state boundaries and
product-build progress against the previous result. Check the manual Blend Plan
for the same case. Existing Total_Feed and lump/fines cases are useful additional
checks when available.

There is no new UI in Task 4; topology diagrams belong to Tasks 31 and 32.
Task 5 has not started. Tasks 1–3 and their uncommitted files remain in place.
