"""Derived material-flow graph for the existing single-feed planning model.

The graph describes possible routes, not actual movements or new constraints.
It is rebuilt from model inputs on request and is never a second owner of solver
state. Conveyor and COS nodes are explicit zero-delay pass-throughs until Task 30.
Simultaneous modes derive physical tipping points with shared source nodes.
"""

from copy import deepcopy
from urllib.parse import quote

from classes.PhaseSchemas import (
    TOPOLOGY_EDGE_CONVEYOR,
    TOPOLOGY_EDGE_COS,
    TOPOLOGY_NODE_CONVEYOR,
    TOPOLOGY_NODE_COS,
    TOPOLOGY_NODE_OPF,
    TOPOLOGY_NODE_PRODUCT_BUILD_LANE,
    TOPOLOGY_NODE_SOURCE,
    TOPOLOGY_NODE_TIPPING_POINT,
    material_flow_topology,
    topology_edge,
    topology_node,
)
from classes.ProductBuildLanes import (
    BYPRODUCT_LANES,
    PRODUCT_LANE,
    normalized_build_lane,
)
from classes.MultiFeedSettings import multi_feed_settings, route_allowed, scoped_builds


def planning_topology(*, multi_feed=None, **kwargs):
    settings = multi_feed_settings(multi_feed)
    if settings["mode"] == "single":
        return one_lane_topology(**kwargs)
    context = kwargs.get("site_context") or {}
    sources = list(kwargs.get("sources") or [])
    nodes, edges = {}, {}
    for point in settings["tipping_points"]:
        allowed = [r for r in sources if r["source_type"] != "stockpile" or route_allowed(settings, r["source"], point["name"])]
        graph = one_lane_topology(**{**kwargs, "sources": allowed,
                                    "site_context": {**context, "opf": point["opf"], "crusher": point["name"]},
                                    "crusher_targets": point["targets_by_period"]})
        remap = {}
        for node in graph["nodes"]:
            props = node["properties"]
            identity = node["node_id"]
            if node["node_type"] == TOPOLOGY_NODE_SOURCE:
                identity = _identifier("source", context.get("hub"), context.get("mine"), props["source_type"], props["source"])
            elif node["node_type"] == TOPOLOGY_NODE_OPF:
                identity = _identifier("opf", context.get("hub"), context.get("mine"), point["opf"])
            elif node["node_type"] == TOPOLOGY_NODE_PRODUCT_BUILD_LANE:
                identity = _identifier("build_lane", context.get("hub"), context.get("mine"), point["opf"], props["lane"])
            remap[node["node_id"]] = identity
        for node in graph["nodes"]:
            item = deepcopy(node)
            item["node_id"] = remap[item["node_id"]]
            for key in ("opf_node_id",):
                if key in item["properties"]:
                    item["properties"][key] = remap[item["properties"][key]]
            if "opf_node_ids" in item["properties"]:
                item["properties"]["opf_node_ids"] = [remap[k] for k in item["properties"]["opf_node_ids"]]
            nodes[item["node_id"]] = item
        for edge in graph["edges"]:
            item = deepcopy(edge)
            item["source_node_id"] = remap[item["source_node_id"]]
            item["target_node_id"] = remap[item["target_node_id"]]
            item["edge_id"] = _identifier("flow", item["source_node_id"], item["target_node_id"])
            edges[item["edge_id"]] = item
    if settings['mode'] == 'combined_opf':
        build_nodes = {key for key, node in nodes.items() if node['node_type'] == TOPOLOGY_NODE_PRODUCT_BUILD_LANE}
        nodes = {key: node for key, node in nodes.items() if key not in build_nodes}
        edges = {key: edge for key, edge in edges.items() if edge['target_node_id'] not in build_nodes}
        builds = scoped_builds(kwargs.get('product_build_settings'), settings)
        for lane in dict.fromkeys(normalized_build_lane(b, kwargs.get('byproducts_enabled', False)) for b in builds):
            indices = [i for i, b in enumerate(builds) if normalized_build_lane(b, kwargs.get('byproducts_enabled', False)) == lane]
            opfs = builds[indices[0]]['contributing_opfs']
            opf_ids = [_identifier('opf', context.get('hub'), context.get('mine'), opf) for opf in opfs]
            identity = _identifier('build_lane', context.get('hub'), context.get('mine'), lane)
            nodes[identity] = topology_node(identity, TOPOLOGY_NODE_PRODUCT_BUILD_LANE, label=lane,
                                           properties=dict(lane=lane, opf_node_ids=opf_ids, build_indices=indices))
            for opf_id in opf_ids:
                edge_id = _identifier('flow', opf_id, identity)
                edges[edge_id] = topology_edge(edge_id, opf_id, identity, latency_hours=0.0)
    return material_flow_topology(_identifier("multi_feed", context.get("hub"), context.get("mine")),
                                  mode="total_feed" if settings["mode"] == "multi_tipping_point" else "combined_opf",
                                  nodes=list(nodes.values()), edges=list(edges.values()),
                                  properties=dict(adapter=settings["mode"], latency_enabled=False,
                                                  shared_physical_balances=True, stockpile_exclusive_tipping_point=True))


def _text(value):
    return "" if value is None else str(value).strip()


def _identifier(kind, *parts):
    # Escape separators so e.g. A:B / C and A / B:C cannot collide.
    return ":".join([kind, *(quote(_text(part), safe="") for part in parts)])


def model_sources(stockpiles, grade_blocks):
    """Describe model sources while preserving payload IDs and APS slice names."""
    for stockpile in stockpiles:
        yield {
            "source": stockpile.name,
            "source_type": "stockpile",
            "source_id": stockpile.name,
            "is_amt": bool(getattr(stockpile, "is_AMT", False)),
        }
    for block in grade_blocks:
        yield {
            "source": getattr(block, "source", None) or block.name,
            "source_type": "grade_block",
            "source_id": block.name,
        }


def one_lane_topology(
    *,
    site_context=None,
    sources=(),
    crusher_targets=None,
    product_build_settings=None,
    byproducts_enabled=False,
):
    """Return a detached PhaseSchemas topology for one current planning lane.

    Sources are descriptors containing ``source``, ``source_type`` (stockpile
    or grade_block), ``source_id`` and optionally ``is_amt``. Repeated payloads
    of the same APS slice share a source node with all payload IDs retained.
    Stockpiles and grade blocks with the same name remain different sources.

    Product lane ``build_indices`` refer to the supplied settings in their
    original order. The adapter neither normalizes nor changes those settings.
    Period targets are copied onto the tipping point; brand ownership is also
    recorded at the OPF. No graph property is read back into the solver.

    Missing site names are explicitly unspecified, supporting older callers.
    IDs depend on site/source identity, not order, plan ID, grades or balances.
    """
    context = site_context or {}
    site = {key: _text(context.get(key)) for key in ("hub", "mine", "opf", "crusher")}
    scope = tuple(site.values())
    topology_id = _identifier("one_lane", *scope)
    tip_id = _identifier("tip", *scope)
    conveyor_id = _identifier("conveyor", *scope)
    cos_id = _identifier("cos", *scope)
    opf_id = _identifier("opf", *scope)
    synthetic = site["crusher"].lower().replace(" ", "_").startswith("total_feed")
    targets = deepcopy(dict(crusher_targets or {}))
    nodes = [
        topology_node(
            tip_id, TOPOLOGY_NODE_TIPPING_POINT,
            label=site["crusher"] or "Unspecified tipping point",
            properties={
                "crusher": site["crusher"],
                "opf_node_id": opf_id,
                "legacy_synthetic_total_feed": synthetic,
                "targets_by_period": targets,
            },
        ),
        topology_node(
            conveyor_id, TOPOLOGY_NODE_CONVEYOR, label="Conveyor",
            properties={"placeholder": True, "latency_enabled": False},
        ),
        topology_node(
            cos_id, TOPOLOGY_NODE_COS, label="COS",
            properties={"placeholder": True, "latency_enabled": False},
        ),
        topology_node(
            opf_id, TOPOLOGY_NODE_OPF,
            label=site["opf"] or "Unspecified OPF",
            properties={
                "opf": site["opf"],
                "brands_by_period": {
                    period: _text(target.get("brand"))
                    for period, target in targets.items()
                },
            },
        ),
    ]
    edges = []

    def connect(source_id, target_id, **kwargs):
        edges.append(topology_edge(
            _identifier("flow", source_id, target_id), source_id, target_id,
            latency_hours=0.0, **kwargs,
        ))

    connect(tip_id, conveyor_id, edge_type=TOPOLOGY_EDGE_CONVEYOR)
    connect(conveyor_id, cos_id, edge_type=TOPOLOGY_EDGE_COS)
    connect(cos_id, opf_id)

    grouped = {}
    for source in sources:
        name = _text(source.get("source"))
        kind = _text(source.get("source_type"))
        if not name:
            raise ValueError("A topology source must have a name.")
        if kind not in {"stockpile", "grade_block"}:
            raise ValueError(f"Unsupported topology source type: {kind!r}.")
        group = grouped.setdefault((kind, name), {"source_ids": set(), "is_amt": False})
        group["source_ids"].add(_text(source.get("source_id")) or name)
        group["is_amt"] |= bool(source.get("is_amt", False))
    for (kind, name), group in sorted(grouped.items()):
        source_id = _identifier("source", *scope, kind, name)
        nodes.append(topology_node(
            source_id, TOPOLOGY_NODE_SOURCE, label=name,
            properties={
                "source": name,
                "source_type": kind,
                "source_ids": sorted(group["source_ids"]),
                "is_amt": group["is_amt"],
            },
        ))
        connect(source_id, tip_id)

    settings = list(product_build_settings or [])
    lanes = BYPRODUCT_LANES if byproducts_enabled else (PRODUCT_LANE,)
    for lane in lanes:
        lane_id = _identifier("build_lane", *scope, lane)
        nodes.append(topology_node(
            lane_id, TOPOLOGY_NODE_PRODUCT_BUILD_LANE, label=lane.title(),
            properties={
                "lane": lane,
                "opf_node_ids": [opf_id],
                "build_indices": [
                    index for index, setting in enumerate(settings)
                    if normalized_build_lane(setting, byproducts_enabled) == lane
                ],
            },
        ))
        connect(opf_id, lane_id)

    return material_flow_topology(
        topology_id, nodes=nodes, edges=edges,
        properties={
            "adapter": "legacy_one_lane",
            "site_context": site,
            "latency_enabled": False,
            "legacy_synthetic_total_feed": synthetic,
            "route_semantics": "candidate_feed",
        },
    )
