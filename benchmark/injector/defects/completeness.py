"""Completeness defects — entity / edge / attribute deletions."""

from __future__ import annotations

import copy
from typing import Any

from injector.defects.base import DefectClass, DefectContext, Projection
from injector.types import EntityRef, Mutation


# Cascading edge-tables to clean up when a node is deleted.
CASCADE_EDGES: dict[str, list[tuple[str, str]]] = {
    "PhysicalRouter": [
        ("PhysicalInterface", "router_id"),
        ("VRF", "router_id"),
    ],
    "PhysicalInterface": [
        ("Interface_Link", "interface_id"),
        ("Subnet_Association", "entity_id"),
    ],
    "PhysicalLink": [
        ("Interface_Link", "link_id"),
    ],
    "Device": [],
}


class MissingNode(DefectClass):
    name = "completeness.missing_node"
    iso_dimension = "completeness"
    default_sid_entity_type = "PhysicalRouter"

    def apply(
        self,
        projection: Projection,
        entity: EntityRef,
        ctx: DefectContext,
        params: dict,
    ) -> Mutation:
        removed = projection.remove(entity.table, entity.id)
        if removed is None:
            return Mutation(kind="noop", before=None, after=None)

        # Cascade interface deletions for router removal (so dependents stay valid)
        if entity.table == "PhysicalRouter":
            # First delete owned interfaces, which themselves cascade to edges.
            owned_interfaces = [
                row["id"]
                for row in list(projection.tables["PhysicalInterface"])
                if row.get("router_id") == entity.id
            ]
            for iface_id in owned_interfaces:
                projection.remove("PhysicalInterface", iface_id)
                _strip_edge(projection, "Interface_Link", "interface_id", iface_id)
                _strip_edge(projection, "Subnet_Association", "entity_id", iface_id)
            # Then dependent VRF + BGP
            owned_vrfs = [
                row["id"]
                for row in list(projection.tables["VRF"])
                if row.get("router_id") == entity.id
            ]
            for vrf_id in owned_vrfs:
                projection.remove("VRF", vrf_id)
                _strip_node(projection, "BGPSession", "vrf_id", vrf_id)
            # And any device whose gateway interface lived on this router
            for dev in list(projection.tables["Device"]):
                if dev.get("interface_id", "").startswith(f"{entity.id}:interface:"):
                    dev["interface_id"] = None

        elif entity.table == "PhysicalInterface":
            _strip_edge(projection, "Interface_Link", "interface_id", entity.id)
            _strip_edge(projection, "Subnet_Association", "entity_id", entity.id)
            for dev in projection.tables["Device"]:
                if dev.get("interface_id") == entity.id:
                    dev["interface_id"] = None

        elif entity.table == "PhysicalLink":
            _strip_edge(projection, "Interface_Link", "link_id", entity.id)

        return Mutation(kind="delete_row", before=removed, after=None)


class MissingEdge(DefectClass):
    name = "completeness.missing_edge"
    iso_dimension = "completeness"
    default_sid_entity_type = "Interface_Link"

    def apply(
        self,
        projection: Projection,
        entity: EntityRef,
        ctx: DefectContext,
        params: dict,
    ) -> Mutation:
        rows = projection.tables[entity.table]
        # Edge id is the natural-key composite produced by base._edge_natural_key
        for i, row in enumerate(rows):
            natural = _edge_id(entity.table, row)
            if natural == entity.id:
                before = copy.deepcopy(row)
                rows.pop(i)
                return Mutation(kind="delete_row", before=before, after=None)
        return Mutation(kind="noop", before=None, after=None)


class MissingAttribute(DefectClass):
    """Set a nullable attribute to None for the chosen entity row."""

    name = "completeness.missing_attribute"
    iso_dimension = "completeness"
    default_sid_entity_type = "PhysicalInterface"

    def apply(
        self,
        projection: Projection,
        entity: EntityRef,
        ctx: DefectContext,
        params: dict,
    ) -> Mutation:
        attr = params.get("attribute")
        if not attr:
            raise ValueError(f"{self.name}: parameters.attribute is required")
        row = projection.find(entity.table, entity.id)
        if row is None or attr not in row:
            return Mutation(kind="noop", before=None, after=None)
        before_val = row.get(attr)
        if before_val is None:
            return Mutation(kind="noop", before=None, after=None)
        before = copy.deepcopy(row)
        row[attr] = None
        return Mutation(kind="null_attribute", before=before, after=copy.deepcopy(row))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_edge(projection: Projection, table: str, attr: str, value: str) -> None:
    projection.tables[table] = [
        row for row in projection.tables[table] if row.get(attr) != value
    ]


def _strip_node(projection: Projection, table: str, attr: str, value: str) -> None:
    projection.tables[table] = [
        row for row in projection.tables[table] if row.get(attr) != value
    ]


def _edge_id(table: str, row: dict[str, Any]) -> str:
    if table == "Interface_Link":
        return f"{row.get('interface_id')}::{row.get('link_id')}"
    if table == "Subnet_Association":
        return f"{row.get('entity_id')}::{row.get('subnet_id')}"
    raise ValueError(table)
