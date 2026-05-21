"""Defect framework — abstract base classes and the shared `Projection` model.

A `Projection` is the mutable "playground" each defect operates on. Both the
catalog and the telemetry projection start as a deep copy of the Golden Twin
entity tables (full attributes, not yet downcast to the catalog/telemetry
attribute slices). After all defects have been applied, the catalog renderer
synthesises the extra inventory columns from the (possibly mutated) entity
rows, and the telemetry renderer drops the catalog-only attributes.

Defects therefore mutate logical entity rows uniformly, and we worry about
projection-specific column slicing only at the serialisation boundary.
"""

from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from random import Random
from typing import Any, ClassVar, Literal

from injector.types import EntityRef, GoldenTwin, GoldenTwinEntities, Mutation


@dataclass
class Projection:
    """Mutable per-projection working set."""

    name: Literal["catalog", "telemetry"]
    tables: dict[str, list[dict[str, Any]]]
    golden: GoldenTwin

    def rows(self, table: str) -> list[dict[str, Any]]:
        return self.tables[table]

    def find(self, table: str, entity_id: str) -> dict[str, Any] | None:
        key = "id"
        if table == "Interface_Link":
            return None  # edge tables are addressed differently
        if table == "Subnet_Association":
            return None
        for row in self.tables[table]:
            if row.get(key) == entity_id:
                return row
        return None

    def remove(self, table: str, entity_id: str) -> dict[str, Any] | None:
        rows = self.tables[table]
        for i, row in enumerate(rows):
            if row.get("id") == entity_id:
                return rows.pop(i)
        return None


def build_projection(
    name: Literal["catalog", "telemetry"], twin: GoldenTwin
) -> Projection:
    """Deep-copy the Golden Twin's entity tables into a mutable projection."""
    tables: dict[str, list[dict[str, Any]]] = {}
    for table, rows in twin.entities.tables():
        tables[table] = [copy.deepcopy(r.model_dump(mode="json")) for r in rows]
    return Projection(name=name, tables=tables, golden=twin)


@dataclass
class DefectContext:
    """Per-defect orchestration context passed to `apply`."""

    rng: Random
    defect_spec_id: str
    scenario_name: str
    seed: int


# ---------------------------------------------------------------------------
# Base classes
# ---------------------------------------------------------------------------


class DefectClass(ABC):
    """Single-projection defect class."""

    name: ClassVar[str]
    iso_dimension: ClassVar[str]
    # Default SID entity-type advertised by this defect; subclasses may override
    # per-entity if multiple entity types are supported (then return the actual
    # one in `_record`).
    default_sid_entity_type: ClassVar[str] = ""

    def applicable_entities(
        self, projection: Projection, params: dict, entity_type: str | None
    ) -> list[EntityRef]:
        """Default: list all rows in the target entity type's table.

        Subclasses can override for finer filtering (e.g. only `Interface_Link`
        rows with two endpoints, only routers of a given role, etc.).
        """
        if not entity_type:
            raise ValueError(
                f"{self.name}: target_entity_type is required for default selection"
            )
        rows = projection.tables.get(entity_type, [])
        refs: list[EntityRef] = []
        for row in rows:
            ent_id = row.get("id")
            if ent_id is None:
                # edge tables: synthesise an identifier from natural keys
                ent_id = _edge_natural_key(entity_type, row)
            refs.append(EntityRef(table=entity_type, id=ent_id))
        return refs

    @abstractmethod
    def apply(
        self,
        projection: Projection,
        entity: EntityRef,
        ctx: DefectContext,
        params: dict,
    ) -> Mutation:
        """Mutate the projection in place and return the mutation record."""


class CrossSourceDefectClass(ABC):
    """Cross-source defect — needs to coordinate between both projections."""

    name: ClassVar[str]
    iso_dimension: ClassVar[str]
    default_sid_entity_type: ClassVar[str] = ""

    def applicable_entities(
        self,
        catalog: Projection,
        telemetry: Projection,
        params: dict,
        entity_type: str | None,
    ) -> list[EntityRef]:
        if not entity_type:
            raise ValueError(
                f"{self.name}: target_entity_type is required for default selection"
            )
        rows = catalog.tables.get(entity_type, [])
        refs: list[EntityRef] = []
        for row in rows:
            ent_id = row.get("id") or _edge_natural_key(entity_type, row)
            refs.append(EntityRef(table=entity_type, id=ent_id))
        return refs

    @abstractmethod
    def apply(
        self,
        catalog: Projection,
        telemetry: Projection,
        entity: EntityRef,
        ctx: DefectContext,
        params: dict,
    ) -> Mutation:
        ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _edge_natural_key(table: str, row: dict[str, Any]) -> str:
    if table == "Interface_Link":
        return f"{row.get('interface_id')}::{row.get('link_id')}"
    if table == "Subnet_Association":
        return f"{row.get('entity_id')}::{row.get('subnet_id')}"
    raise ValueError(f"no natural key defined for {table}")


def select_entities(
    refs: list[EntityRef],
    selection,
    rng: Random,
    projection: Projection | None = None,
) -> list[EntityRef]:
    """Apply the SelectionSpec to a list of candidates."""
    # Optional attribute filter
    if selection.filter and projection is not None:
        refs = [
            r
            for r in refs
            if _row_passes_filter(projection.find(r.table, r.id) or {}, selection.filter)
        ]

    if selection.mode == "explicit_ids":
        wanted = set(selection.ids or [])
        return [r for r in refs if r.id in wanted]

    if selection.mode == "random_rate":
        rate = selection.rate or 0.0
        return [r for r in refs if rng.random() < rate]

    if selection.mode == "fixed_count":
        count = selection.count or 0
        if count >= len(refs):
            return list(refs)
        return rng.sample(refs, count)

    raise ValueError(f"unknown selection mode {selection.mode}")


def _row_passes_filter(row: dict[str, Any], flt: dict[str, list[Any]]) -> bool:
    for attr, allowed in flt.items():
        if row.get(attr) not in allowed:
            return False
    return True
