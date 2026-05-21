"""Golden Twin → Telemetry JSON (Spanner-shaped, "as-running" view).

Drops fields a CSV-side inventory invents (e.g. router location, device
gateway/vlan). See plan §5.3.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from injector.types import GoldenTwin


# Per-entity attribute allow-lists.
ATTR_ALLOW: dict[str, list[str]] = {
    "PhysicalRouter": ["id", "name", "vendor", "model", "role", "status"],
    "PhysicalInterface": [
        "id",
        "router_id",
        "name",
        "speed",
        "media_type",
        "ip_address",
        "mac_address",
        "status",
    ],
    "PhysicalLink": ["id", "name", "bandwidth", "status"],
    "Interface_Link": ["interface_id", "link_id"],
    "Device": ["id", "name", "interface_id", "network_name", "ip_address", "status"],
    "LogicalSubnet": ["id", "cidr", "network_type"],
    "Subnet_Association": ["entity_id", "subnet_id", "entity_type"],
    "L3VPNService": ["id", "name", "service_type", "topology", "status"],
    "VRF": ["id", "router_id", "vpn_id", "name", "rd", "status"],
    "BGPSession": ["id", "vrf_id", "local_as", "remote_as", "peer_ip", "status"],
}


def _project_row(row: dict[str, Any], allow: list[str]) -> dict[str, Any]:
    return {k: row.get(k) for k in allow}


def build_telemetry_payload(twin: GoldenTwin) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for table, rows in twin.entities.tables():
        allow = ATTR_ALLOW[table]
        if table in {"Interface_Link", "Subnet_Association"}:
            sort_key = (
                (lambda r: (r["interface_id"], r["link_id"]))
                if table == "Interface_Link"
                else (lambda r: (r["entity_id"], r["subnet_id"]))
            )
        else:
            sort_key = lambda r: r["id"]
        projected = [_project_row(r.model_dump(mode="json"), allow) for r in rows]
        projected.sort(key=sort_key)
        out[table] = projected
    return out


def to_telemetry(twin: GoldenTwin, output_path: Path) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_telemetry_payload(twin)
    output_path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")
