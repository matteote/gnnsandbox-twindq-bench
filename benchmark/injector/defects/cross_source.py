"""Cross-source defects — coordinated mutations across catalog + telemetry."""

from __future__ import annotations

import copy

from injector.defects.accuracy import DEFAULT_DRIFT_TABLES
from injector.defects.base import (
    CrossSourceDefectClass,
    DefectContext,
    Projection,
)
from injector.types import EntityRef, Mutation


class AttributeConflict(CrossSourceDefectClass):
    """Apply opposing attribute mutations to catalog vs telemetry."""

    name = "cross_source.attribute_conflict"
    iso_dimension = "accuracy"
    default_sid_entity_type = "PhysicalRouter"

    def apply(
        self,
        catalog: Projection,
        telemetry: Projection,
        entity: EntityRef,
        ctx: DefectContext,
        params: dict,
    ) -> Mutation:
        attr = params.get("attribute")
        if not attr:
            raise ValueError(f"{self.name}: parameters.attribute is required")
        cat_row = catalog.find(entity.table, entity.id)
        tel_row = telemetry.find(entity.table, entity.id)
        if cat_row is None or tel_row is None:
            return Mutation(kind="noop", before=None, after=None)
        original = cat_row.get(attr)
        if original is None:
            return Mutation(kind="noop", before=None, after=None)
        drift = params.get("drift_table") or DEFAULT_DRIFT_TABLES.get(attr) or []
        choices = [v for v in drift if v != original]
        if len(choices) < 2:
            return Mutation(kind="noop", before=None, after=None)
        cat_new, tel_new = ctx.rng.sample(choices, 2)
        before = {"catalog": copy.deepcopy(cat_row), "telemetry": copy.deepcopy(tel_row)}
        cat_row[attr] = cat_new
        tel_row[attr] = tel_new
        return Mutation(
            kind="set_attribute_both",
            before=before,
            after={
                "catalog": copy.deepcopy(cat_row),
                "telemetry": copy.deepcopy(tel_row),
            },
        )


class StructuralConflict(CrossSourceDefectClass):
    """Catalog says A↔B; telemetry says A↔C."""

    name = "cross_source.structural_conflict"
    iso_dimension = "consistency"
    default_sid_entity_type = "Interface_Link"

    def applicable_entities(
        self,
        catalog: Projection,
        telemetry: Projection,
        params: dict,
        entity_type: str | None,
    ) -> list[EntityRef]:
        # Operate on PhysicalLink ids
        return [
            EntityRef(table="PhysicalLink", id=link["id"])
            for link in catalog.tables.get("PhysicalLink", [])
        ]

    def apply(
        self,
        catalog: Projection,
        telemetry: Projection,
        entity: EntityRef,
        ctx: DefectContext,
        params: dict,
    ) -> Mutation:
        link_id = entity.id
        tel_rows = telemetry.tables["Interface_Link"]
        endpoints = [r for r in tel_rows if r.get("link_id") == link_id]
        if len(endpoints) < 2:
            return Mutation(kind="noop", before=None, after=None)
        victim = ctx.rng.choice(endpoints)
        link_iface_ids = {r["interface_id"] for r in endpoints}
        all_iface_ids = [i["id"] for i in telemetry.tables["PhysicalInterface"]]
        candidates = [iid for iid in all_iface_ids if iid not in link_iface_ids]
        if not candidates:
            return Mutation(kind="noop", before=None, after=None)
        new_iface = ctx.rng.choice(candidates)
        before = copy.deepcopy(victim)
        victim["interface_id"] = new_iface
        return Mutation(
            kind="endpoint_shift_telemetry",
            before=before,
            after=copy.deepcopy(victim),
        )


class ExistenceConflict(CrossSourceDefectClass):
    """Entity present in one projection only — delete from the other."""

    name = "cross_source.existence_conflict"
    iso_dimension = "completeness"
    default_sid_entity_type = "PhysicalRouter"

    def apply(
        self,
        catalog: Projection,
        telemetry: Projection,
        entity: EntityRef,
        ctx: DefectContext,
        params: dict,
    ) -> Mutation:
        drop_from = params.get("drop_from", "telemetry")
        victim_proj = telemetry if drop_from == "telemetry" else catalog
        removed = victim_proj.remove(entity.table, entity.id)
        if removed is None:
            return Mutation(kind="noop", before=None, after=None)
        return Mutation(kind=f"delete_from_{drop_from}", before=removed, after=None)


class PhantomEntity(CrossSourceDefectClass):
    """Add an entity to the catalog that does not exist in the Golden Twin."""

    name = "cross_source.phantom_entity"
    iso_dimension = "accuracy"
    default_sid_entity_type = "PhysicalRouter"

    def applicable_entities(
        self,
        catalog: Projection,
        telemetry: Projection,
        params: dict,
        entity_type: str | None,
    ) -> list[EntityRef]:
        # No source rows needed — phantom defects fabricate. Generate placeholders
        # so the selection layer can use rate/count semantics.
        target = entity_type or self.default_sid_entity_type
        n = params.get("max_phantoms", len(catalog.tables.get(target, [])))
        return [
            EntityRef(table=target, id=f"__phantom_slot__{i}")
            for i in range(max(1, n))
        ]

    def apply(
        self,
        catalog: Projection,
        telemetry: Projection,
        entity: EntityRef,
        ctx: DefectContext,
        params: dict,
    ) -> Mutation:
        return _fabricate(catalog, entity.table, ctx, "catalog")


class ShadowEntity(CrossSourceDefectClass):
    """Add an entity to the telemetry that does not exist in the Golden Twin."""

    name = "cross_source.shadow_entity"
    iso_dimension = "completeness"
    default_sid_entity_type = "PhysicalRouter"

    def applicable_entities(
        self,
        catalog: Projection,
        telemetry: Projection,
        params: dict,
        entity_type: str | None,
    ) -> list[EntityRef]:
        target = entity_type or self.default_sid_entity_type
        n = params.get("max_shadows", len(telemetry.tables.get(target, [])))
        return [
            EntityRef(table=target, id=f"__shadow_slot__{i}")
            for i in range(max(1, n))
        ]

    def apply(
        self,
        catalog: Projection,
        telemetry: Projection,
        entity: EntityRef,
        ctx: DefectContext,
        params: dict,
    ) -> Mutation:
        return _fabricate(telemetry, entity.table, ctx, "telemetry")


def _fabricate(
    projection: Projection,
    table: str,
    ctx: DefectContext,
    side: str,
) -> Mutation:
    tag = f"{side[0].upper()}{ctx.rng.randint(10000, 99999)}"
    if table == "PhysicalRouter":
        row = {
            "id": f"router:phantom-{tag}",
            "name": f"phantom-{tag}",
            "vendor": "VyOS",
            "model": "Virtual",
            "location_city": None,
            "location_lat": None,
            "location_lon": None,
            "role": "PE",
            "status": "Running",
            "config": None,
        }
    elif table == "Device":
        row = {
            "id": f"device:phantom-{tag}",
            "name": f"phantom-{tag}",
            "interface_id": None,
            "network_name": None,
            "ip_address": None,
            "mgmt_ip": None,
            "gateway": None,
            "vlan": None,
            "status": "Ready",
            "config": None,
        }
    else:
        return Mutation(kind="noop", before=None, after=None)
    projection.tables[table].append(row)
    # rewrite EntityRef.id at the ledger layer to use the new fabricated id
    return Mutation(kind=f"insert_{side}", before=None, after=copy.deepcopy(row))
