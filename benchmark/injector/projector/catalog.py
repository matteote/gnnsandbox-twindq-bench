"""Golden Twin → Catalog CSV bundle (the "manually-maintained inventory" view).

Naming-convention drift between catalog and telemetry is intentional. See
plan §5.2 and §12.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pandas as pd

from injector.types import GoldenTwin, PhysicalInterface, PhysicalRouter


SPEED_TO_MBPS = {
    "10G": 10000,
    "1G": 1000,
    "100M": 100,
    "10M": 10,
    "100mbit": 100,
    "1gbit": 1000,
    "10gbit": 10000,
    "unlimited": 1000,
}


def to_display_name(hostname: str) -> str:
    """Catalog naming convention: `pe1` → `PE-1`, `ce1-hub` → `CE-1-HUB`, etc.

    Splits the leading alpha prefix from the trailing alphanumerics, upper-cases
    the prefix, and inserts dashes between alpha/numeric boundaries.
    """
    parts = hostname.split("-")
    out_parts = []
    for part in parts:
        m = re.match(r"^([a-zA-Z]+)(\d+)$", part)
        if m:
            out_parts.append(f"{m.group(1).upper()}-{m.group(2)}")
        else:
            out_parts.append(part.upper())
    return "-".join(out_parts)


def _deterministic_int(seed: int, key: str, modulo: int) -> int:
    digest = hashlib.sha256(f"{seed}:{key}".encode()).digest()
    return int.from_bytes(digest[:4], "big") % modulo


def _synth_serial(seed: int, router_id: str) -> str:
    digest = hashlib.sha256(f"{seed}:serial:{router_id}".encode()).hexdigest()
    return f"SN-{digest[:12].upper()}"


def _synth_asset_tag(seed: int, router_id: str) -> str:
    n = _deterministic_int(seed, f"asset:{router_id}", 9000) + 1000
    return f"ASSET-{n:04d}"


def _synth_mgmt_ip(seq: int) -> str:
    return f"10.0.0.{seq + 10}"


def _parse_speed_mbps(speed: str | None) -> int | None:
    if not speed:
        return None
    return SPEED_TO_MBPS.get(speed)


def _bandwidth_to_mbps(bandwidth: str | None) -> int | None:
    if not bandwidth:
        return None
    return SPEED_TO_MBPS.get(bandwidth)


def _routers_df(twin: GoldenTwin, seed: int) -> pd.DataFrame:
    rows: list[dict] = []
    for seq, r in enumerate(sorted(twin.entities.routers, key=lambda x: x.id)):
        rows.append(
            {
                "inventory_id": f"INV-{seq + 1:04d}",
                "hostname": r.name,
                "display_name": to_display_name(r.name),
                "vendor": r.vendor,
                "model": r.model,
                "serial_number": _synth_serial(seed, r.id),
                "asset_tag": _synth_asset_tag(seed, r.id),
                "site": r.location_city,
                "latitude": r.location_lat,
                "longitude": r.location_lon,
                "role": r.role,
                "mgmt_ip": _synth_mgmt_ip(seq),
                "last_audit_date": _last_audit_date(twin, seed, r.id),
            }
        )
    return pd.DataFrame(rows)


def _last_audit_date(twin: GoldenTwin, seed: int, router_id: str) -> str:
    """ISO date, default `captured_at` minus 30 days, jittered ±5 days by hash."""
    import datetime as _dt

    base = _dt.datetime.fromisoformat(twin.captured_at.replace("Z", "+00:00"))
    base_date = (base - _dt.timedelta(days=30)).date()
    jitter = _deterministic_int(seed, f"audit:{router_id}", 11) - 5
    return (base_date + _dt.timedelta(days=jitter)).isoformat()


def _interfaces_df(twin: GoldenTwin) -> pd.DataFrame:
    router_by_id = {r.id: r for r in twin.entities.routers}
    rows: list[dict] = []
    for seq, i in enumerate(sorted(twin.entities.interfaces, key=lambda x: x.id)):
        router = router_by_id.get(i.router_id)
        rows.append(
            {
                "inventory_id": f"IF-{seq + 1:04d}",
                "device_hostname": router.name if router else None,
                "interface_name": i.name,
                "speed_mbps": _parse_speed_mbps(i.speed),
                "media_type": i.media_type,
                "planned_ip": i.ip_address,
                "mac_address": i.mac_address,
                "description": _interface_description(i, router),
            }
        )
    return pd.DataFrame(rows)


def _interface_description(iface: PhysicalInterface, router: PhysicalRouter | None) -> str:
    if router is None:
        return f"Interface {iface.name}"
    role = router.role or "?"
    return f"{role} {router.name} :: {iface.name}"


def _links_df(twin: GoldenTwin) -> pd.DataFrame:
    iface_by_id = {i.id: i for i in twin.entities.interfaces}
    router_by_id = {r.id: r for r in twin.entities.routers}
    endpoints: dict[str, list] = {}
    for il in twin.entities.interface_link:
        endpoints.setdefault(il.link_id, []).append(il.interface_id)

    rows: list[dict] = []
    for seq, link in enumerate(sorted(twin.entities.links, key=lambda x: x.id)):
        ep_ids = sorted(endpoints.get(link.id, []))
        ep = []
        for iid in ep_ids[:2]:
            iface = iface_by_id.get(iid)
            router = router_by_id.get(iface.router_id) if iface else None
            ep.append((router.name if router else None, iface.name if iface else None))
        while len(ep) < 2:
            ep.append((None, None))
        rows.append(
            {
                "inventory_id": f"LINK-{seq + 1:04d}",
                "circuit_id": f"CKT-{link.name}",
                "endpoint_a_device": ep[0][0],
                "endpoint_a_interface": ep[0][1],
                "endpoint_b_device": ep[1][0],
                "endpoint_b_interface": ep[1][1],
                "bandwidth_mbps": _bandwidth_to_mbps(link.bandwidth),
                "fiber_type": _synth_fiber_type(link.name),
                "length_km": _synth_length_km(link.name),
            }
        )
    return pd.DataFrame(rows)


def _synth_fiber_type(link_name: str) -> str:
    types = ["single-mode", "multi-mode", "copper"]
    return types[_deterministic_int(0, f"fiber:{link_name}", len(types))]


def _synth_length_km(link_name: str) -> int:
    return _deterministic_int(0, f"length:{link_name}", 500) + 1


def _devices_df(twin: GoldenTwin) -> pd.DataFrame:
    iface_by_id = {i.id: i for i in twin.entities.interfaces}
    router_by_id = {r.id: r for r in twin.entities.routers}
    rows: list[dict] = []
    for d in sorted(twin.entities.devices, key=lambda x: x.id):
        iface = iface_by_id.get(d.interface_id) if d.interface_id else None
        router = router_by_id.get(iface.router_id) if iface else None
        rows.append(
            {
                "inventory_id": d.id,
                "device_name": d.name,
                "network_name": d.network_name,
                "ip_address": d.ip_address,
                "mgmt_ip": d.mgmt_ip,
                "gateway": d.gateway,
                "gateway_router": router.name if router else None,
                "gateway_interface": iface.name if iface else None,
                "vlan": d.vlan,
            }
        )
    return pd.DataFrame(rows)


def _vpns_df(twin: GoldenTwin) -> pd.DataFrame:
    services_by_id = {s.id: s for s in twin.entities.l3vpn_services}
    rows: list[dict] = []
    for vrf in sorted(twin.entities.vrfs, key=lambda x: x.id):
        svc = services_by_id.get(vrf.vpn_id)
        rows.append(
            {
                "vpn_id": vrf.vpn_id,
                "vpn_name": svc.name if svc else None,
                "service_type": svc.service_type if svc else None,
                "topology": svc.topology if svc else None,
                "vrf_id": vrf.id,
                "vrf_name": vrf.name,
                "router_id": vrf.router_id,
                "rd": vrf.rd,
                "status": vrf.status,
            }
        )
    return pd.DataFrame(rows)


CSV_TABLES = {
    "routers.csv": _routers_df,
    "interfaces.csv": _interfaces_df,
    "links.csv": _links_df,
    "devices.csv": _devices_df,
    "vpns.csv": _vpns_df,
}


def to_catalog(twin: GoldenTwin, output_dir: Path, seed: int) -> None:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, builder in CSV_TABLES.items():
        if filename == "routers.csv":
            df = builder(twin, seed)
        else:
            df = builder(twin)
        df.to_csv(output_dir / filename, index=False, lineterminator="\n")
