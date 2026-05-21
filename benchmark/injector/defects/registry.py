"""Mapping from defect class name → implementation."""

from __future__ import annotations

from injector.defects.accuracy import AttributeDrift, EndpointShift
from injector.defects.base import CrossSourceDefectClass, DefectClass
from injector.defects.completeness import (
    MissingAttribute,
    MissingEdge,
    MissingNode,
)
from injector.defects.consistency import AsymmetricLink, OrphanEntity
from injector.defects.cross_source import (
    AttributeConflict,
    ExistenceConflict,
    PhantomEntity,
    ShadowEntity,
    StructuralConflict,
)
from injector.defects.uniqueness import MergedIdentity, SplitIdentity
from injector.defects.validity import InvalidEnum, MalformedId, OutOfRange


SINGLE_PROJECTION_REGISTRY: dict[str, type[DefectClass]] = {
    MissingNode.name: MissingNode,
    MissingEdge.name: MissingEdge,
    MissingAttribute.name: MissingAttribute,
    AttributeDrift.name: AttributeDrift,
    EndpointShift.name: EndpointShift,
    AsymmetricLink.name: AsymmetricLink,
    OrphanEntity.name: OrphanEntity,
    SplitIdentity.name: SplitIdentity,
    MergedIdentity.name: MergedIdentity,
    MalformedId.name: MalformedId,
    OutOfRange.name: OutOfRange,
    InvalidEnum.name: InvalidEnum,
}

CROSS_SOURCE_REGISTRY: dict[str, type[CrossSourceDefectClass]] = {
    AttributeConflict.name: AttributeConflict,
    StructuralConflict.name: StructuralConflict,
    ExistenceConflict.name: ExistenceConflict,
    PhantomEntity.name: PhantomEntity,
    ShadowEntity.name: ShadowEntity,
}


REGISTRY: dict[str, type] = {
    **SINGLE_PROJECTION_REGISTRY,
    **CROSS_SOURCE_REGISTRY,
}


def resolve(name: str):
    """Instantiate a defect class by registry name."""
    cls = REGISTRY.get(name)
    if cls is None:
        raise KeyError(f"Unknown defect class: {name!r}")
    return cls()


def is_cross_source(name: str) -> bool:
    return name in CROSS_SOURCE_REGISTRY
