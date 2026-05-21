"""Snapshot a Golden Twin from a live Spanner database (Mode A).

Requires the `spanner` optional dependency. Reads the currently-active row
(`valid_end_ts IS NULL`) from each topology table.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from injector.loader import compute_content_hash
from injector.types import (
    BGPSession,
    Device,
    GoldenTwin,
    GoldenTwinEntities,
    GoldenTwinSource,
    InterfaceLink,
    L3VPNService,
    LogicalSubnet,
    PhysicalInterface,
    PhysicalLink,
    PhysicalRouter,
    SubnetAssociation,
    VRF,
)


def _utc_now_iso() -> str:
    return (
        _dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def _run_query(database: Any, sql: str) -> list[tuple[Any, ...]]:
    with database.snapshot() as snap:
        return list(snap.execute_sql(sql))


def snapshot_from_spanner(
    *, project: str, instance: str, database: str, network_name: str
) -> GoldenTwin:
    try:
        from google.cloud import spanner  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "snapshot --from-spanner requires the `spanner` extra. "
            "Install with: pip install 'twindq-bench[spanner]'"
        ) from exc

    client = spanner.Client(project=project)
    db = client.instance(instance).database(database)

    routers = [
        PhysicalRouter(
            id=r[0],
            name=r[1],
            vendor=r[2],
            model=r[3],
            location_city=r[4],
            location_lat=r[5],
            location_lon=r[6],
            role=r[7],
            status=r[8],
        )
        for r in _run_query(
            db,
            "SELECT id,name,vendor,model,location_city,location_lat,location_lon,"
            "role,status FROM PhysicalRouter WHERE valid_end_ts IS NULL",
        )
    ]
    interfaces = [
        PhysicalInterface(
            id=r[0], router_id=r[1], name=r[2], speed=r[3], media_type=r[4],
            ip_address=r[5], mac_address=r[6], status=r[7],
        )
        for r in _run_query(
            db,
            "SELECT id,router_id,name,speed,media_type,ip_address,mac_address,status "
            "FROM PhysicalInterface WHERE valid_end_ts IS NULL",
        )
    ]
    links = [
        PhysicalLink(id=r[0], name=r[1], bandwidth=r[2], status=r[3])
        for r in _run_query(
            db,
            "SELECT id,name,bandwidth,status FROM PhysicalLink "
            "WHERE valid_end_ts IS NULL",
        )
    ]
    edges = [
        InterfaceLink(interface_id=r[0], link_id=r[1])
        for r in _run_query(
            db,
            "SELECT interface_id,link_id FROM Interface_Link "
            "WHERE valid_end_ts IS NULL",
        )
    ]
    devices = [
        Device(
            id=r[0], name=r[1], interface_id=r[2], network_name=r[3],
            ip_address=r[4], mgmt_ip=r[5], gateway=r[6], vlan=r[7], status=r[8],
        )
        for r in _run_query(
            db,
            "SELECT id,name,interface_id,network_name,ip_address,mgmt_ip,gateway,"
            "vlan,status FROM Device WHERE valid_end_ts IS NULL",
        )
    ]
    subnets = [
        LogicalSubnet(id=r[0], cidr=r[1], network_type=r[2])
        for r in _run_query(
            db,
            "SELECT id,cidr,network_type FROM LogicalSubnet "
            "WHERE valid_end_ts IS NULL",
        )
    ]
    assocs = [
        SubnetAssociation(entity_id=r[0], subnet_id=r[1], entity_type=r[2])
        for r in _run_query(
            db,
            "SELECT entity_id,subnet_id,entity_type FROM Subnet_Association "
            "WHERE valid_end_ts IS NULL",
        )
    ]
    services = [
        L3VPNService(
            id=r[0], customer_id=r[1], name=r[2], service_type=r[3],
            topology=r[4], status=r[5],
        )
        for r in _run_query(
            db,
            "SELECT id,customer_id,name,service_type,topology,status FROM L3VPNService "
            "WHERE valid_end_ts IS NULL",
        )
    ]
    vrfs = [
        VRF(
            id=r[0], router_id=r[1], vpn_id=r[2], name=r[3], rd=r[4], status=r[5],
        )
        for r in _run_query(
            db,
            "SELECT id,router_id,vpn_id,name,rd,status FROM VRF "
            "WHERE valid_end_ts IS NULL",
        )
    ]
    bgp = [
        BGPSession(
            id=r[0], vrf_id=r[1], local_as=r[2], remote_as=r[3],
            peer_ip=r[4], status=r[5],
        )
        for r in _run_query(
            db,
            "SELECT id,vrf_id,local_as,remote_as,peer_ip,status FROM BGPSession "
            "WHERE valid_end_ts IS NULL",
        )
    ]

    twin = GoldenTwin(
        captured_at=_utc_now_iso(),
        source=GoldenTwinSource(
            mode="from_spanner",
            network_name=network_name,
            spanner_database=f"{project}/{instance}/{database}",
        ),
        entities=GoldenTwinEntities(
            routers=routers,
            interfaces=interfaces,
            links=links,
            interface_link=edges,
            devices=devices,
            subnets=subnets,
            subnet_association=assocs,
            l3vpn_services=services,
            vrfs=vrfs,
            bgp_sessions=bgp,
        ),
    )
    twin.content_hash = compute_content_hash(twin)
    return twin
