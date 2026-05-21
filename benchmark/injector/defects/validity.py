"""Validity defects — malformed ids, out-of-range, invalid enum."""

from __future__ import annotations

import copy

from injector.defects.base import DefectClass, DefectContext, Projection
from injector.types import EntityRef, Mutation


class MalformedId(DefectClass):
    """Violate the `:`-delimited id convention."""

    name = "validity.malformed_id"
    iso_dimension = "validity"
    default_sid_entity_type = "PhysicalRouter"

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
        before = copy.deepcopy(row)
        bad_id = entity.id.replace(":", "_")
        row["id"] = bad_id
        # Note: cascading references intentionally NOT updated — the whole point
        # of this defect is dangling references that the system under test should
        # flag.
        return Mutation(kind="rename_id", before=before, after=copy.deepcopy(row))


class OutOfRange(DefectClass):
    """Set value outside legal range (`speed=99999G`, `vlan=99999`)."""

    name = "validity.out_of_range"
    iso_dimension = "validity"
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
        attr = params.get("attribute")
        if attr is None:
            attr = "speed" if entity.table == "PhysicalInterface" else "vlan"
        if attr not in row:
            return Mutation(kind="noop", before=None, after=None)
        before = copy.deepcopy(row)
        if attr == "speed":
            row[attr] = "99999G"
        elif attr == "vlan":
            row[attr] = 99999
        else:
            row[attr] = params.get("value", "OUT_OF_RANGE")
        return Mutation(kind="set_attribute", before=before, after=copy.deepcopy(row))


class InvalidEnum(DefectClass):
    """Set value outside the enum (`role=FOO`, `status=BANANA`)."""

    name = "validity.invalid_enum"
    iso_dimension = "validity"
    default_sid_entity_type = "PhysicalRouter"

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
        attr = params.get("attribute")
        if attr is None:
            attr = "role" if entity.table == "PhysicalRouter" else "status"
        if attr not in row:
            return Mutation(kind="noop", before=None, after=None)
        before = copy.deepcopy(row)
        invalid_values = {
            "role": "FOO",
            "status": "BANANA",
        }
        row[attr] = params.get("value", invalid_values.get(attr, "INVALID"))
        return Mutation(kind="set_attribute", before=before, after=copy.deepcopy(row))
