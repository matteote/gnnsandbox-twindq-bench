"""Pydantic data contracts for the TwinDQ-Bench injector.

Mirrors §5.1 of the implementation plan: one model per topology entity, plus the
container `GoldenTwin`, the `DefectRecord` ledger row, and the `Scenario` schema.

Strict mode (`extra="forbid"`) everywhere — unknown fields are a contract bug
and must fail loudly.
"""

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


class PhysicalRouter(_Strict):
    id: str
    name: str
    vendor: str | None = None
    model: str | None = None
    location_city: str | None = None
    location_lat: float | None = None
    location_lon: float | None = None
    role: str | None = None
    status: str | None = None
    config: dict[str, Any] | None = None


class PhysicalInterface(_Strict):
    id: str
    router_id: str
    name: str
    speed: str | None = None
    media_type: str | None = None
    ip_address: str | None = None
    mac_address: str | None = None
    status: str | None = None


class PhysicalLink(_Strict):
    id: str
    name: str
    bandwidth: str | None = None
    status: str | None = None
    properties: dict[str, Any] | None = None


class InterfaceLink(_Strict):
    interface_id: str
    link_id: str


class Device(_Strict):
    id: str
    name: str
    interface_id: str | None = None
    network_name: str | None = None
    ip_address: str | None = None
    mgmt_ip: str | None = None
    gateway: str | None = None
    vlan: int | None = None
    status: str | None = None
    config: dict[str, Any] | None = None


class LogicalSubnet(_Strict):
    id: str
    cidr: str
    network_type: str | None = None
    description: str | None = None
    mtu: int | None = None
    properties: dict[str, Any] | None = None


class SubnetAssociation(_Strict):
    entity_id: str
    subnet_id: str
    entity_type: str


class L3VPNService(_Strict):
    id: str
    name: str
    customer_id: str
    service_type: str | None = None
    topology: str | None = None
    status: str | None = None
    config: dict[str, Any] | None = None


class VRF(_Strict):
    id: str
    router_id: str
    vpn_id: str
    name: str
    rd: str | None = None
    status: str | None = None
    config: dict[str, Any] | None = None


class BGPSession(_Strict):
    id: str
    vrf_id: str
    local_as: int | None = None
    remote_as: int | None = None
    peer_ip: str | None = None
    status: str | None = None
    config: dict[str, Any] | None = None


# Per-entity tag map. Used by the loader to know which model to instantiate per
# table, and by the projector to know which dimension a row belongs to.
ENTITY_MODELS: dict[str, type[_Strict]] = {
    "PhysicalRouter": PhysicalRouter,
    "PhysicalInterface": PhysicalInterface,
    "PhysicalLink": PhysicalLink,
    "Interface_Link": InterfaceLink,
    "Device": Device,
    "LogicalSubnet": LogicalSubnet,
    "Subnet_Association": SubnetAssociation,
    "L3VPNService": L3VPNService,
    "VRF": VRF,
    "BGPSession": BGPSession,
}


# ---------------------------------------------------------------------------
# Golden Twin container
# ---------------------------------------------------------------------------


class GoldenTwinSource(_Strict):
    mode: Literal["from_yaml", "from_spanner"]
    network_name: str
    yaml_paths: list[str] = Field(default_factory=list)
    spanner_database: str | None = None


# Field names cannot shadow the entity class names in the class body — pydantic
# resolves the annotation in that namespace and would see the Field() object
# instead of the model. So we use attribute names like `routers`, `interfaces`,
# etc., and serialise/deserialise via aliases that match the table names in §5.1.
class GoldenTwinEntities(_Strict):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    routers: list[PhysicalRouter] = Field(default_factory=list, alias="PhysicalRouter")
    interfaces: list[PhysicalInterface] = Field(
        default_factory=list, alias="PhysicalInterface"
    )
    links: list[PhysicalLink] = Field(default_factory=list, alias="PhysicalLink")
    interface_link: list[InterfaceLink] = Field(
        default_factory=list, alias="Interface_Link"
    )
    devices: list[Device] = Field(default_factory=list, alias="Device")
    subnets: list[LogicalSubnet] = Field(default_factory=list, alias="LogicalSubnet")
    subnet_association: list[SubnetAssociation] = Field(
        default_factory=list, alias="Subnet_Association"
    )
    l3vpn_services: list[L3VPNService] = Field(
        default_factory=list, alias="L3VPNService"
    )
    vrfs: list[VRF] = Field(default_factory=list, alias="VRF")
    bgp_sessions: list[BGPSession] = Field(default_factory=list, alias="BGPSession")

    # Mapping from §5.1 table name to the attribute name on this model.
    TABLE_TO_ATTR: ClassVar[dict[str, str]] = {
        "PhysicalRouter": "routers",
        "PhysicalInterface": "interfaces",
        "PhysicalLink": "links",
        "Interface_Link": "interface_link",
        "Device": "devices",
        "LogicalSubnet": "subnets",
        "Subnet_Association": "subnet_association",
        "L3VPNService": "l3vpn_services",
        "VRF": "vrfs",
        "BGPSession": "bgp_sessions",
    }

    def table(self, name: str) -> list:
        """Return the row list for the given §5.1 table name."""
        return getattr(self, self.TABLE_TO_ATTR[name])

    def tables(self):
        """Iterate (table_name, rows) pairs in plan order."""
        for table_name, attr in self.TABLE_TO_ATTR.items():
            yield table_name, getattr(self, attr)


class GoldenTwin(_Strict):
    schema_version: str = "1.0"
    captured_at: str
    source: GoldenTwinSource
    content_hash: str | None = None
    entities: GoldenTwinEntities


# ---------------------------------------------------------------------------
# Defect ledger
# ---------------------------------------------------------------------------


class EntityRef(_Strict):
    table: str
    id: str


class Mutation(_Strict):
    kind: str
    before: Any | None = None
    after: Any | None = None
    # For edges we cannot use a single `id`; allow explicit before/after of the
    # edge tuple via the existing before/after fields.


class DefectRecord(_Strict):
    defect_id: str
    scenario: str
    seed: int
    defect_class: str
    iso_dimension: str
    sid_entity_type: str
    target_projection: Literal["catalog", "telemetry", "both"]
    target_entity: EntityRef
    mutation: Mutation
    applied_at: str


# ---------------------------------------------------------------------------
# Scenario schema
# ---------------------------------------------------------------------------


class SelectionSpec(_Strict):
    mode: Literal["random_rate", "fixed_count", "explicit_ids"]
    rate: float | None = None
    count: int | None = None
    ids: list[str] | None = None
    filter: dict[str, list[Any]] | None = None


class DefectSpec(_Strict):
    id: str
    class_: str = Field(alias="class")
    target_projection: Literal["catalog", "telemetry", "both"]
    target_entity_type: str | None = None
    selection: SelectionSpec
    parameters: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ScenarioInputs(_Strict):
    golden_twin: str


class ScenarioOutputs(_Strict):
    root: str
    catalog_dir: str
    telemetry_path: str
    ledger_path: str


class Scenario(_Strict):
    name: str
    description: str | None = None
    seed: int
    inputs: ScenarioInputs
    outputs: ScenarioOutputs
    defects: list[DefectSpec]
