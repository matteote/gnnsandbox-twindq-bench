from __future__ import annotations

from random import Random

from injector.defects.base import DefectContext, build_projection
from injector.defects.consistency import AsymmetricLink, OrphanEntity
from injector.types import EntityRef


def _ctx(seed: int = 0) -> DefectContext:
    return DefectContext(rng=Random(seed), defect_spec_id="t", scenario_name="t", seed=seed)


def test_asymmetric_link_drops_one_endpoint(tiny_twin) -> None:
    proj = build_projection("catalog", tiny_twin)
    link_id = "link:pe1-pe2"
    before_count = sum(1 for il in proj.tables["Interface_Link"] if il["link_id"] == link_id)
    assert before_count == 2
    AsymmetricLink().apply(
        proj, EntityRef(table="PhysicalLink", id=link_id), _ctx(3), {}
    )
    after_count = sum(1 for il in proj.tables["Interface_Link"] if il["link_id"] == link_id)
    assert after_count == 1


def test_orphan_entity_rewrites_router_id(tiny_twin) -> None:
    proj = build_projection("telemetry", tiny_twin)
    OrphanEntity().apply(
        proj,
        EntityRef(table="PhysicalInterface", id="router:pe1:interface:eth1"),
        _ctx(4),
        {},
    )
    row = proj.find("PhysicalInterface", "router:pe1:interface:eth1")
    assert row["router_id"].startswith("router:GHOST-")
