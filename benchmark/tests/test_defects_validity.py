from __future__ import annotations

from random import Random

from injector.defects.base import DefectContext, build_projection
from injector.defects.validity import InvalidEnum, MalformedId, OutOfRange
from injector.types import EntityRef


def _ctx(seed: int = 0) -> DefectContext:
    return DefectContext(rng=Random(seed), defect_spec_id="t", scenario_name="t", seed=seed)


def test_malformed_id_breaks_convention(tiny_twin) -> None:
    proj = build_projection("catalog", tiny_twin)
    MalformedId().apply(
        proj, EntityRef(table="PhysicalRouter", id="router:pe1"), _ctx(), {}
    )
    bad_rows = [r for r in proj.tables["PhysicalRouter"] if r["id"] == "router_pe1"]
    assert len(bad_rows) == 1


def test_out_of_range_speed(tiny_twin) -> None:
    proj = build_projection("telemetry", tiny_twin)
    OutOfRange().apply(
        proj,
        EntityRef(table="PhysicalInterface", id="router:pe1:interface:eth1"),
        _ctx(),
        {"attribute": "speed"},
    )
    row = proj.find("PhysicalInterface", "router:pe1:interface:eth1")
    assert row["speed"] == "99999G"


def test_invalid_enum_role(tiny_twin) -> None:
    proj = build_projection("catalog", tiny_twin)
    InvalidEnum().apply(
        proj, EntityRef(table="PhysicalRouter", id="router:pe1"), _ctx(), {"attribute": "role"}
    )
    row = proj.find("PhysicalRouter", "router:pe1")
    assert row["role"] == "FOO"
