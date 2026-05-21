from __future__ import annotations

from random import Random

from injector.defects.base import DefectContext, build_projection
from injector.defects.completeness import MissingAttribute, MissingEdge, MissingNode
from injector.types import EntityRef


def _ctx(seed: int = 0) -> DefectContext:
    return DefectContext(rng=Random(seed), defect_spec_id="t", scenario_name="t", seed=seed)


def test_missing_node_router_cascades(tiny_twin) -> None:
    proj = build_projection("catalog", tiny_twin)
    defect = MissingNode()
    ref = EntityRef(table="PhysicalRouter", id="router:pe1")
    mutation = defect.apply(proj, ref, _ctx(), {})
    assert mutation.kind == "delete_row"
    assert mutation.before["id"] == "router:pe1"
    # Cascading interfaces removed
    assert all(i["router_id"] != "router:pe1" for i in proj.tables["PhysicalInterface"])
    # Cascading edges removed
    assert all(
        not il["interface_id"].startswith("router:pe1:")
        for il in proj.tables["Interface_Link"]
    )


def test_missing_node_unrelated_entities_untouched(tiny_twin) -> None:
    proj = build_projection("telemetry", tiny_twin)
    defect = MissingNode()
    before_pe2 = [r for r in proj.tables["PhysicalRouter"] if r["id"] == "router:pe2"][0]
    defect.apply(proj, EntityRef(table="PhysicalRouter", id="router:pe1"), _ctx(), {})
    after_pe2 = [r for r in proj.tables["PhysicalRouter"] if r["id"] == "router:pe2"][0]
    assert before_pe2 == after_pe2


def test_missing_node_deterministic(tiny_twin) -> None:
    p1 = build_projection("catalog", tiny_twin)
    p2 = build_projection("catalog", tiny_twin)
    MissingNode().apply(p1, EntityRef(table="PhysicalRouter", id="router:pe1"), _ctx(7), {})
    MissingNode().apply(p2, EntityRef(table="PhysicalRouter", id="router:pe1"), _ctx(7), {})
    assert p1.tables == p2.tables


def test_missing_edge_removes_endpoint(tiny_twin) -> None:
    proj = build_projection("catalog", tiny_twin)
    edge = proj.tables["Interface_Link"][0]
    nat_key = f"{edge['interface_id']}::{edge['link_id']}"
    mutation = MissingEdge().apply(
        proj, EntityRef(table="Interface_Link", id=nat_key), _ctx(), {}
    )
    assert mutation.kind == "delete_row"
    assert edge not in proj.tables["Interface_Link"]


def test_missing_attribute_sets_null(tiny_twin) -> None:
    proj = build_projection("catalog", tiny_twin)
    defect = MissingAttribute()
    mutation = defect.apply(
        proj,
        EntityRef(table="PhysicalInterface", id="router:pe1:interface:eth1"),
        _ctx(),
        {"attribute": "mac_address"},
    )
    assert mutation.kind == "null_attribute"
    row = [i for i in proj.tables["PhysicalInterface"] if i["id"] == "router:pe1:interface:eth1"][0]
    assert row["mac_address"] is None


def test_missing_attribute_iso_dimension() -> None:
    assert MissingNode.iso_dimension == "completeness"
    assert MissingEdge.iso_dimension == "completeness"
    assert MissingAttribute.iso_dimension == "completeness"
