"""Consistency defects — asymmetric links and orphans."""

from __future__ import annotations

import copy

from injector.defects.base import DefectClass, DefectContext, Projection
from injector.types import EntityRef, Mutation


class AsymmetricLink(DefectClass):
    """Delete exactly one of the two Interface_Link rows for a link."""

    name = "consistency.asymmetric_link"
    iso_dimension = "consistency"
    default_sid_entity_type = "Interface_Link"

    def apply(
        self,
        projection: Projection,
        entity: EntityRef,
        ctx: DefectContext,
        params: dict,
    ) -> Mutation:
        # entity.id refers to the link id (we sample link ids in this defect).
        # Treat both natural-key form and bare link-id form.
        link_id = entity.id
        if "::" in link_id:
            # caller passed a natural key — extract link id half
            link_id = link_id.split("::", 1)[1]
        rows = projection.tables["Interface_Link"]
        endpoints = [
            (i, r) for i, r in enumerate(rows) if r.get("link_id") == link_id
        ]
        if len(endpoints) < 2:
            return Mutation(kind="noop", before=None, after=None)
        idx, victim = ctx.rng.choice(endpoints)
        before = copy.deepcopy(victim)
        rows.pop(idx)
        return Mutation(kind="delete_row", before=before, after=None)

    def applicable_entities(
        self, projection: Projection, params: dict, entity_type: str | None
    ) -> list[EntityRef]:
        # Operate over PhysicalLink ids — easier to sample than Interface_Link rows.
        refs: list[EntityRef] = []
        for link in projection.tables.get("PhysicalLink", []):
            refs.append(EntityRef(table="PhysicalLink", id=link["id"]))
        return refs


class OrphanEntity(DefectClass):
    """Rewrite `router_id` (or `interface_id`) to a non-existent id."""

    name = "consistency.orphan_entity"
    iso_dimension = "consistency"
    default_sid_entity_type = "PhysicalInterface"

    def apply(
        self,
        projection: Projection,
        entity: EntityRef,
        ctx: DefectContext,
        params: dict,
    ) -> Mutation:
        row = projection.find(entity.table, entity.id)
        if row is None:
            return Mutation(kind="noop", before=None, after=None)
        if entity.table == "PhysicalInterface":
            attr = "router_id"
            ghost = f"router:GHOST-{ctx.rng.randint(1000, 9999)}"
        elif entity.table == "Device":
            attr = "interface_id"
            ghost = (
                f"router:GHOST-{ctx.rng.randint(1000, 9999)}:interface:eth0"
            )
        else:
            raise ValueError(f"{self.name} not applicable to {entity.table}")
        before = copy.deepcopy(row)
        row[attr] = ghost
        return Mutation(kind="set_attribute", before=before, after=copy.deepcopy(row))
