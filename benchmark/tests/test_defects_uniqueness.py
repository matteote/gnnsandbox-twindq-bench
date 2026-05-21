from __future__ import annotations

from random import Random

from injector.defects.base import DefectContext, build_projection
from injector.defects.uniqueness import MergedIdentity, SplitIdentity
from injector.types import EntityRef


def _ctx(seed: int = 0) -> DefectContext:
    return DefectContext(rng=Random(seed), defect_spec_id="t", scenario_name="t", seed=seed)


def test_split_identity_adds_clone_with_different_id(tiny_twin) -> None:
    proj = build_projection("catalog", tiny_twin)
    before_ids = {r["id"] for r in proj.tables["PhysicalRouter"]}
    SplitIdentity().apply(
        proj, EntityRef(table="PhysicalRouter", id="router:pe1"), _ctx(0), {}
    )
    after_ids = {r["id"] for r in proj.tables["PhysicalRouter"]}
    new_ids = after_ids - before_ids
    assert len(new_ids) == 1
    new_id = new_ids.pop()
    assert new_id != "router:pe1"
    assert new_id.startswith("router:PE-")


def test_split_identity_noop_on_telemetry(tiny_twin) -> None:
    proj = build_projection("telemetry", tiny_twin)
    before = list(proj.tables["PhysicalRouter"])
    SplitIdentity().apply(
        proj, EntityRef(table="PhysicalRouter", id="router:pe1"), _ctx(0), {}
    )
    assert proj.tables["PhysicalRouter"] == before


def test_merged_identity_removes_victim(tiny_twin) -> None:
    proj = build_projection("catalog", tiny_twin)
    before_count = len(proj.tables["PhysicalRouter"])
    MergedIdentity().apply(
        proj, EntityRef(table="PhysicalRouter", id="router:pe1"), _ctx(0), {}
    )
    assert len(proj.tables["PhysicalRouter"]) == before_count - 1
