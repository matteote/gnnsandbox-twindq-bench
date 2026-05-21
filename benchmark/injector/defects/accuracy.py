"""Accuracy defects — attribute drift and endpoint shifts."""

from __future__ import annotations

import copy

from injector.defects.base import DefectClass, DefectContext, Projection
from injector.types import EntityRef, Mutation


DEFAULT_DRIFT_TABLES: dict[str, list] = {
    "vendor": ["Cisco", "Juniper", "Nokia", "Huawei", "Arista"],
    "model": ["MX480", "ASR9K", "7750-SR", "NE40E", "7280R"],
    "speed": ["10G", "1G", "100M"],
    "role": ["PE", "P", "CE", "RR"],
    "status": ["UP", "DOWN", "ADMIN_DOWN"],
}


class AttributeDrift(DefectClass):
    """Replace a scalar attribute with an implausible-but-typed value."""

    name = "accuracy.attribute_drift"
    iso_dimension = "accuracy"
    default_sid_entity_type = "PhysicalRouter"

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
        drift_table = params.get("drift_table") or DEFAULT_DRIFT_TABLES.get(attr)
        if not drift_table:
            raise ValueError(
                f"{self.name}: no drift_table for attribute {attr!r}; pass one in parameters"
            )
        choices = [v for v in drift_table if v != before_val]
        if not choices:
            return Mutation(kind="noop", before=None, after=None)
        new_val = ctx.rng.choice(choices)
        before = copy.deepcopy(row)
        row[attr] = new_val
        return Mutation(kind="set_attribute", before=before, after=copy.deepcopy(row))


class EndpointShift(DefectClass):
    """Rewrite one endpoint of a link to a different (still existing) interface."""

    name = "accuracy.endpoint_shift"
    iso_dimension = "accuracy"
    default_sid_entity_type = "Interface_Link"

    def apply(
        self,
        projection: Projection,
        entity: EntityRef,
        ctx: DefectContext,
        params: dict,
    ) -> Mutation:
        # entity.id is the Interface_Link natural key. Pick a fresh interface id
        # not already on this link.
        rows = projection.tables["Interface_Link"]
        target_row = None
        for row in rows:
            if f"{row.get('interface_id')}::{row.get('link_id')}" == entity.id:
                target_row = row
                break
        if target_row is None:
            return Mutation(kind="noop", before=None, after=None)
        link_id = target_row["link_id"]
        siblings = {
            r["interface_id"] for r in rows if r.get("link_id") == link_id
        }
        all_iface_ids = [i["id"] for i in projection.tables["PhysicalInterface"]]
        candidates = [iid for iid in all_iface_ids if iid not in siblings]
        if not candidates:
            return Mutation(kind="noop", before=None, after=None)
        new_iface = ctx.rng.choice(candidates)
        before = copy.deepcopy(target_row)
        target_row["interface_id"] = new_iface
        return Mutation(
            kind="set_attribute", before=before, after=copy.deepcopy(target_row)
        )
