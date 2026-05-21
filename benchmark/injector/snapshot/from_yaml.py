"""Build a Golden Twin from VyOSInfrastructure / VyOSL3VPN YAMLs.

Implements Appendix C of the implementation plan. The parser does not call into
the operator code — it independently re-derives the CRD → entity mapping.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
from pathlib import Path
from typing import Any

import yaml

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


# Per plan §C.5: bandwidth string → catalog `speed_map`.
SPEED_MAP = {
    "1gbit": "1G",
    "10gbit": "10G",
    "100mbit": "100M",
    "10mbit": "10M",
    "unlimited": "1G",
}


def _utc_now_iso() -> str:
    return (
        _dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def _synth_mac(interface_id: str) -> str:
    digest = hashlib.sha256(interface_id.encode()).hexdigest()
    octets = ":".join(digest[i : i + 2] for i in range(0, 10, 2))
    return f"02:{octets}"


def _load_yaml_files(network_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """Return {kind: [doc, ...]} for the files we care about."""
    buckets: dict[str, list[dict[str, Any]]] = {
        "VyOSInfrastructure": [],
        "VyOSL3VPN": [],
    }
    for path in sorted(network_dir.glob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text()):
            if not isinstance(doc, dict):
                continue
            kind = doc.get("kind")
            if kind in buckets:
                buckets[kind].append(doc)
    return buckets


# ---------------------------------------------------------------------------
# Index builders
# ---------------------------------------------------------------------------


def _build_ip_index(
    networks: list[dict[str, Any]],
) -> dict[tuple[str, str], tuple[str, str | None]]:
    """(router_name, interface_name) → (network_name, ip_address)."""
    idx: dict[tuple[str, str], tuple[str, str | None]] = {}
    for net in networks:
        net_name = net["name"]
        for cr in net.get("connected_routers", []) or []:
            key = (cr["router_name"], cr["interface"])
            idx[key] = (net_name, cr.get("ip_address"))
    return idx


# ---------------------------------------------------------------------------
# Topology builders
# ---------------------------------------------------------------------------


def _build_routers(routers_yaml: list[dict[str, Any]]) -> list[PhysicalRouter]:
    out: list[PhysicalRouter] = []
    for r in routers_yaml:
        name = r["name"]
        loc = r.get("location") or {}
        out.append(
            PhysicalRouter(
                id=f"router:{name}",
                name=name,
                vendor="VyOS",
                model="Virtual",
                location_city=loc.get("city"),
                location_lat=loc.get("latitude"),
                location_lon=loc.get("longitude"),
                role=r.get("role"),
                status="Running",
                config={
                    "hostname": r.get("hostname"),
                    "router_id": r.get("router_id"),
                    "country": loc.get("country"),
                    "site": loc.get("site"),
                },
            )
        )
    return out


def _build_interfaces(
    routers_yaml: list[dict[str, Any]],
    networks_yaml: list[dict[str, Any]],
    ip_index: dict[tuple[str, str], tuple[str, str | None]],
) -> list[PhysicalInterface]:
    networks_by_name = {n["name"]: n for n in networks_yaml}
    out: list[PhysicalInterface] = []
    for r in routers_yaml:
        router_name = r["name"]
        router_id = f"router:{router_name}"
        router_loopback = (r.get("router_id") or "").strip() or None
        for iface in r.get("interfaces", []) or []:
            iface_name = iface["name"]
            iface_id = f"{router_id}:interface:{iface_name}"
            net_name = iface.get("network")
            net = networks_by_name.get(net_name) if net_name else None
            speed = None
            ip = None
            if net is not None:
                speed = SPEED_MAP.get((net.get("bandwidth") or "").lower())
                if net.get("network_type") == "loopback":
                    ip = router_loopback
                else:
                    _, ip = ip_index.get((router_name, iface_name), (None, None))
            out.append(
                PhysicalInterface(
                    id=iface_id,
                    router_id=router_id,
                    name=iface_name,
                    speed=speed,
                    media_type="ethernet",
                    ip_address=ip,
                    mac_address=_synth_mac(iface_id),
                    status="UP",
                )
            )
    return out


def _build_links_and_edges(
    networks_yaml: list[dict[str, Any]],
) -> tuple[list[PhysicalLink], list[InterfaceLink]]:
    links: list[PhysicalLink] = []
    edges: list[InterfaceLink] = []
    for net in networks_yaml:
        if net.get("network_type") != "p2p":
            continue
        connected = net.get("connected_routers") or []
        if len(connected) != 2:
            # plan §C.6: only well-formed p2p networks produce a link.
            continue
        link_id = f"link:{net['name']}"
        links.append(
            PhysicalLink(
                id=link_id,
                name=net["name"],
                bandwidth=net.get("bandwidth"),
                status="UP",
            )
        )
        for cr in connected:
            edges.append(
                InterfaceLink(
                    interface_id=f"router:{cr['router_name']}:interface:{cr['interface']}",
                    link_id=link_id,
                )
            )
    return links, edges


def _build_subnets_and_associations(
    networks_yaml: list[dict[str, Any]],
    routers_yaml: list[dict[str, Any]],
) -> tuple[list[LogicalSubnet], list[SubnetAssociation]]:
    subnets: list[LogicalSubnet] = []
    assocs: list[SubnetAssociation] = []

    for net in networks_yaml:
        ntype = net.get("network_type")
        if ntype == "loopback":
            # plan §C.7: one subnet per router's loopback IP.
            for r in routers_yaml:
                lo_id = (r.get("router_id") or "").strip()
                if not lo_id:
                    continue
                # Only emit the association if the router actually has a lo iface
                # on this loopback network.
                has_lo = any(
                    (iface.get("name") == "lo" and iface.get("network") == net["name"])
                    for iface in r.get("interfaces", []) or []
                )
                if not has_lo:
                    continue
                subnet_id = f"subnet:{lo_id}/32"
                subnets.append(
                    LogicalSubnet(
                        id=subnet_id,
                        cidr=f"{lo_id}/32",
                        network_type="loopback",
                    )
                )
                assocs.append(
                    SubnetAssociation(
                        entity_id=f"router:{r['name']}:interface:lo",
                        subnet_id=subnet_id,
                        entity_type="Interface",
                    )
                )
            continue

        subnet_id = f"subnet:{net['name']}"
        subnets.append(
            LogicalSubnet(
                id=subnet_id,
                cidr=net.get("subnet") or "",
                network_type=ntype,
            )
        )
        for cr in net.get("connected_routers") or []:
            assocs.append(
                SubnetAssociation(
                    entity_id=f"router:{cr['router_name']}:interface:{cr['interface']}",
                    subnet_id=subnet_id,
                    entity_type="Interface",
                )
            )

    return subnets, assocs


def _build_devices(
    devices_yaml: list[dict[str, Any]],
    interfaces: list[PhysicalInterface],
) -> list[Device]:
    iface_by_ip = {iface.ip_address: iface.id for iface in interfaces if iface.ip_address}
    out: list[Device] = []
    for d in devices_yaml:
        name = d["name"]
        gw = d.get("gateway")
        out.append(
            Device(
                id=f"device:{name}",
                name=name,
                interface_id=iface_by_ip.get(gw) if gw else None,
                network_name=d.get("network_name"),
                ip_address=d.get("ip_address"),
                mgmt_ip=d.get("mgmt_ip"),
                gateway=gw,
                vlan=None,
                status="Ready",
            )
        )
    return out


# ---------------------------------------------------------------------------
# L3VPN builders
# ---------------------------------------------------------------------------


def _build_l3vpn(
    vpn_docs: list[dict[str, Any]],
) -> tuple[list[L3VPNService], list[VRF], list[BGPSession]]:
    services: dict[str, L3VPNService] = {}
    vrfs: list[VRF] = []
    bgp: list[BGPSession] = []

    for doc in vpn_docs:
        spec = doc.get("spec") or {}
        for svc in spec.get("services") or []:
            svc_name = svc["name"]
            services[svc_name] = L3VPNService(
                id=f"vpn:{svc_name}",
                name=svc_name,
                customer_id="cust:default",
                service_type=svc.get("type"),
                topology=svc.get("topology"),
                status="Ready",
            )

        for router in spec.get("routers") or []:
            r_name = router["name"]
            for vrf_spec in router.get("vrfs") or []:
                v_name = vrf_spec["name"]
                vrfs.append(
                    VRF(
                        id=f"vrf:{r_name}:{v_name}",
                        router_id=f"router:{r_name}",
                        vpn_id=f"vpn:{v_name}",
                        name=f"VRF-{v_name}",
                        rd=vrf_spec.get("rd"),
                        status="Active",
                        config={
                            "table": vrf_spec.get("table"),
                            "rt_export": vrf_spec.get("rt_export"),
                            "rt_import": vrf_spec.get("rt_import"),
                            "interfaces": vrf_spec.get("interfaces"),
                        },
                    )
                )
            bgp_block = router.get("bgp") or {}
            for vrf_bgp in bgp_block.get("vrfs") or []:
                v_name = vrf_bgp["name"]
                for neighbor in vrf_bgp.get("neighbors") or []:
                    peer = neighbor.get("peer")
                    bgp.append(
                        BGPSession(
                            id=f"bgp:{r_name}:{v_name}:{peer}",
                            vrf_id=f"vrf:{r_name}:{v_name}",
                            local_as=65001,
                            remote_as=neighbor.get("remote_as"),
                            peer_ip=peer,
                            status="Established",
                        )
                    )

    return list(services.values()), vrfs, bgp


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def snapshot_from_yaml(network_dir: Path) -> GoldenTwin:
    network_dir = Path(network_dir)
    if not network_dir.is_dir():
        raise FileNotFoundError(f"Not a directory: {network_dir}")

    docs = _load_yaml_files(network_dir)
    infra_docs = docs["VyOSInfrastructure"]
    vpn_docs = docs["VyOSL3VPN"]
    if not infra_docs:
        raise ValueError(f"No VyOSInfrastructure YAML found under {network_dir}")

    # Aggregate from all VyOSInfrastructure docs (usually exactly one)
    routers_yaml: list[dict[str, Any]] = []
    networks_yaml: list[dict[str, Any]] = []
    devices_yaml: list[dict[str, Any]] = []
    for d in infra_docs:
        spec = d.get("spec") or {}
        routers_yaml.extend(spec.get("routers") or [])
        networks_yaml.extend(spec.get("networks") or [])
        devices_yaml.extend(spec.get("devices") or [])

    ip_index = _build_ip_index(networks_yaml)
    routers = _build_routers(routers_yaml)
    interfaces = _build_interfaces(routers_yaml, networks_yaml, ip_index)
    links, edges = _build_links_and_edges(networks_yaml)
    subnets, assocs = _build_subnets_and_associations(networks_yaml, routers_yaml)
    devices = _build_devices(devices_yaml, interfaces)
    services, vrfs, bgp = _build_l3vpn(vpn_docs)

    network_name = network_dir.name

    twin = GoldenTwin(
        captured_at=_utc_now_iso(),
        source=GoldenTwinSource(
            mode="from_yaml",
            network_name=network_name,
            yaml_paths=sorted(str(p) for p in network_dir.glob("*.yaml")),
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
