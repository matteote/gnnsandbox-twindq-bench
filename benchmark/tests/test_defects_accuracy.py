from __future__ import annotations

from random import Random

from injector.defects.accuracy import AttributeDrift, EndpointShift
from injector.defects.base import DefectContext, build_projection
from injector.types import EntityRef


def _ctx(seed: int = 0) -> DefectContext:
    return DefectContext(rng=Random(seed), defect_spec_id="t", scenario_name="t", seed=seed)


def test_attribute_drift_changes_value(tiny_twin) -> None:
    proj = build_projection("catalog", tiny_twin)
    ref = EntityRef(table="PhysicalRouter", id="router:pe1")
    before_vendor = proj.find("PhysicalRouter", "router:pe1")["vendor"]
    mutation = AttributeDrift().apply(
        proj, ref, _ctx(1), {"attribute": "vendor"}
    )
    assert mutation.kind == "set_attribute"
    new_vendor = proj.find("PhysicalRouter", "router:pe1")["vendor"]
    assert new_vendor != before_vendor


def test_attribute_drift_deterministic(tiny_twin) -> None:
    p1 = build_projection("catalog", tiny_twin)
    p2 = build_projection("catalog", tiny_twin)
    AttributeDrift().apply(
        p1, EntityRef(table="PhysicalRouter", id="router:pe1"), _ctx(99), {"attribute": "vendor"}
    )
    AttributeDrift().apply(
        p2, EntityRef(table="PhysicalRouter", id="router:pe1"), _ctx(99), {"attribute": "vendor"}
    )
    assert p1.tables == p2.tables


def test_endpoint_shift_rewrites_one_endpoint(tiny_twin) -> None:
    proj = build_projection("catalog", tiny_twin)
    edge = proj.tables["Interface_Link"][0]
    nat_key = f"{edge['interface_id']}::{edge['link_id']}"
    mutation = EndpointShift().apply(
        proj, EntityRef(table="Interface_Link", id=nat_key), _ctx(2), {}
    )
    assert mutation.kind == "set_attribute"
    assert mutation.before["interface_id"] != mutation.after["interface_id"]
    assert mutation.before["link_id"] == mutation.after["link_id"]
