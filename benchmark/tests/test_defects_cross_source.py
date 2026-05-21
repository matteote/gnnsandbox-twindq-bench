from __future__ import annotations

from random import Random

from injector.defects.base import DefectContext, build_projection
from injector.defects.cross_source import (
    AttributeConflict,
    ExistenceConflict,
    PhantomEntity,
    ShadowEntity,
    StructuralConflict,
)
from injector.types import EntityRef


def _ctx(seed: int = 0) -> DefectContext:
    return DefectContext(rng=Random(seed), defect_spec_id="t", scenario_name="t", seed=seed)


def test_attribute_conflict_diverges_projections(tiny_twin) -> None:
    cat = build_projection("catalog", tiny_twin)
    tel = build_projection("telemetry", tiny_twin)
    AttributeConflict().apply(
        cat,
        tel,
        EntityRef(table="PhysicalRouter", id="router:pe1"),
        _ctx(11),
        {"attribute": "vendor", "drift_table": ["Cisco", "Juniper", "Nokia", "Huawei"]},
    )
    cat_v = cat.find("PhysicalRouter", "router:pe1")["vendor"]
    tel_v = tel.find("PhysicalRouter", "router:pe1")["vendor"]
    assert cat_v != tel_v


def test_structural_conflict_shifts_telemetry_only(tiny_twin) -> None:
    cat = build_projection("catalog", tiny_twin)
    tel = build_projection("telemetry", tiny_twin)
    StructuralConflict().apply(
        cat,
        tel,
        EntityRef(table="PhysicalLink", id="link:pe1-pe2"),
        _ctx(13),
        {},
    )
    cat_eps = sorted(
        il["interface_id"] for il in cat.tables["Interface_Link"]
        if il["link_id"] == "link:pe1-pe2"
    )
    tel_eps = sorted(
        il["interface_id"] for il in tel.tables["Interface_Link"]
        if il["link_id"] == "link:pe1-pe2"
    )
    assert cat_eps != tel_eps


def test_existence_conflict_drops_telemetry_row(tiny_twin) -> None:
    cat = build_projection("catalog", tiny_twin)
    tel = build_projection("telemetry", tiny_twin)
    ExistenceConflict().apply(
        cat,
        tel,
        EntityRef(table="Device", id="device:dev1"),
        _ctx(),
        {"drop_from": "telemetry"},
    )
    assert any(d["id"] == "device:dev1" for d in cat.tables["Device"])
    assert not any(d["id"] == "device:dev1" for d in tel.tables["Device"])


def test_phantom_entity_adds_to_catalog_only(tiny_twin) -> None:
    cat = build_projection("catalog", tiny_twin)
    tel = build_projection("telemetry", tiny_twin)
    PhantomEntity().apply(
        cat,
        tel,
        EntityRef(table="PhysicalRouter", id="__phantom_slot__0"),
        _ctx(5),
        {},
    )
    cat_ids = {r["id"] for r in cat.tables["PhysicalRouter"]}
    tel_ids = {r["id"] for r in tel.tables["PhysicalRouter"]}
    extras = cat_ids - tel_ids
    assert any(i.startswith("router:phantom-") for i in extras)


def test_shadow_entity_adds_to_telemetry_only(tiny_twin) -> None:
    cat = build_projection("catalog", tiny_twin)
    tel = build_projection("telemetry", tiny_twin)
    ShadowEntity().apply(
        cat,
        tel,
        EntityRef(table="Device", id="__shadow_slot__0"),
        _ctx(7),
        {},
    )
    tel_ids = {r["id"] for r in tel.tables["Device"]}
    cat_ids = {r["id"] for r in cat.tables["Device"]}
    extras = tel_ids - cat_ids
    assert any(i.startswith("device:phantom-") for i in extras)
