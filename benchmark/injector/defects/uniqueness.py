"""Uniqueness defects — split or merged identities (catalog-side)."""

from __future__ import annotations

import copy

from injector.defects.base import DefectClass, DefectContext, Projection
from injector.projector.catalog import to_display_name
from injector.types import EntityRef, Mutation


class SplitIdentity(DefectClass):
    """Introduce a renamed clone of an existing entity using a different convention.

    e.g. an extra `router:PE-1` row alongside the canonical `router:pe1`.
    """

    name = "uniqueness.split_identity"
    iso_dimension = "uniqueness"
    default_sid_entity_type = "PhysicalRouter"

    def apply(
        self,
        projection: Projection,
        entity: EntityRef,
        ctx: DefectContext,
        params: dict,
    ) -> Mutation:
        if projection.name != "catalog":
            # plan §6: this defect is catalog-only — silently noop elsewhere.
            return Mutation(kind="noop", before=None, after=None)
        row = projection.find(entity.table, entity.id)
        if row is None:
            return Mutation(kind="noop", before=None, after=None)
        original_name = row.get("name") or ""
        clone_name = to_display_name(original_name)
        if clone_name == original_name:
            clone_name = f"{original_name}-DUP"
        clone = copy.deepcopy(row)
        if entity.table == "PhysicalRouter":
            clone["id"] = f"router:{clone_name}"
            clone["name"] = clone_name
        elif entity.table == "Device":
            clone["id"] = f"device:{clone_name}"
            clone["name"] = clone_name
        else:
            raise ValueError(f"{self.name} not applicable to {entity.table}")
        projection.tables[entity.table].append(clone)
        return Mutation(kind="duplicate_row", before=None, after=clone)


class MergedIdentity(DefectClass):
    """Collapse two distinct entities into one row (catalog-only)."""

    name = "uniqueness.merged_identity"
    iso_dimension = "uniqueness"
    default_sid_entity_type = "PhysicalRouter"

    def apply(
        self,
        projection: Projection,
        entity: EntityRef,
        ctx: DefectContext,
        params: dict,
    ) -> Mutation:
        if projection.name != "catalog":
            return Mutation(kind="noop", before=None, after=None)
        rows = projection.tables.get(entity.table, [])
        target = projection.find(entity.table, entity.id)
        if target is None:
            return Mutation(kind="noop", before=None, after=None)
        # pick a victim — any other entity of the same type
        victims = [r for r in rows if r.get("id") and r["id"] != entity.id]
        if not victims:
            return Mutation(kind="noop", before=None, after=None)
        victim = ctx.rng.choice(victims)
        # Mutate target by absorbing victim's name; remove victim.
        before = {
            "target": copy.deepcopy(target),
            "victim": copy.deepcopy(victim),
        }
        target["name"] = f"{target['name']}+{victim.get('name', '')}"
        projection.tables[entity.table] = [
            r for r in rows if r.get("id") != victim["id"]
        ]
        return Mutation(kind="merge_rows", before=before, after=copy.deepcopy(target))
