"""Golden Twin (de)serialisation + integrity validation.

The loader is deliberately picky: any missing cross-reference or shape violation
aborts the load. A broken Golden Twin propagates bad ground truth to the
defect ledger, which would invalidate every downstream benchmark run.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from injector.types import GoldenTwin


ID_PATTERNS = {
    "PhysicalRouter": re.compile(r"^router:[^:]+$"),
    "PhysicalInterface": re.compile(r"^router:[^:]+:interface:[^:]+$"),
    "PhysicalLink": re.compile(r"^link:[^:]+$"),
    "Device": re.compile(r"^device:[^:]+$"),
    "L3VPNService": re.compile(r"^vpn:[^:]+$"),
    "VRF": re.compile(r"^vrf:[^:]+:[^:]+$"),
    "BGPSession": re.compile(r"^bgp:[^:]+:[^:]+:.+$"),
    "LogicalSubnet": re.compile(r"^subnet:.+$"),
}


def _canonical_entities_bytes(twin: GoldenTwin) -> bytes:
    """Stable byte serialisation of entities, used for the content_hash."""
    payload = twin.entities.model_dump(mode="json", by_alias=True)
    for table in payload:
        if isinstance(payload[table], list):
            payload[table] = sorted(
                payload[table],
                key=lambda row: json.dumps(row, sort_keys=True, default=str),
            )
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def compute_content_hash(twin: GoldenTwin) -> str:
    digest = hashlib.sha256(_canonical_entities_bytes(twin)).hexdigest()
    return f"sha256:{digest}"


def _check_id_patterns(twin: GoldenTwin, errors: list[str]) -> None:
    for table, pattern in ID_PATTERNS.items():
        for row in twin.entities.table(table):
            if not pattern.match(row.id):
                errors.append(f"{table} id violates convention: {row.id!r}")


def _check_referential_integrity(twin: GoldenTwin, errors: list[str]) -> None:
    e = twin.entities
    router_ids = {r.id for r in e.routers}
    interface_ids = {i.id for i in e.interfaces}
    link_ids = {l.id for l in e.links}
    subnet_ids = {s.id for s in e.subnets}
    vpn_ids = {v.id for v in e.l3vpn_services}
    vrf_ids = {v.id for v in e.vrfs}

    for iface in e.interfaces:
        if iface.router_id not in router_ids:
            errors.append(
                f"PhysicalInterface {iface.id} references unknown router_id "
                f"{iface.router_id}"
            )

    for il in e.interface_link:
        if il.interface_id not in interface_ids:
            errors.append(
                f"Interface_Link references unknown interface_id {il.interface_id}"
            )
        if il.link_id not in link_ids:
            errors.append(f"Interface_Link references unknown link_id {il.link_id}")

    # Every link must have exactly two endpoints
    endpoints_per_link: dict[str, int] = {lid: 0 for lid in link_ids}
    for il in e.interface_link:
        if il.link_id in endpoints_per_link:
            endpoints_per_link[il.link_id] += 1
    for lid, count in endpoints_per_link.items():
        if count != 2:
            errors.append(f"PhysicalLink {lid} has {count} endpoints, expected 2")

    for dev in e.devices:
        if dev.interface_id and dev.interface_id not in interface_ids:
            errors.append(
                f"Device {dev.id} references unknown interface_id {dev.interface_id}"
            )

    for sa in e.subnet_association:
        if sa.subnet_id not in subnet_ids:
            errors.append(
                f"Subnet_Association references unknown subnet_id {sa.subnet_id}"
            )

    for vrf in e.vrfs:
        if vrf.router_id not in router_ids:
            errors.append(f"VRF {vrf.id} references unknown router_id {vrf.router_id}")
        if vrf.vpn_id not in vpn_ids:
            errors.append(f"VRF {vrf.id} references unknown vpn_id {vrf.vpn_id}")

    for bgp in e.bgp_sessions:
        if bgp.vrf_id not in vrf_ids:
            errors.append(
                f"BGPSession {bgp.id} references unknown vrf_id {bgp.vrf_id}"
            )


def validate_golden_twin(twin: GoldenTwin, *, check_hash: bool = True) -> None:
    """Raise ValueError describing every contract violation found, or return."""
    errors: list[str] = []
    _check_id_patterns(twin, errors)
    _check_referential_integrity(twin, errors)
    if check_hash and twin.content_hash is not None:
        expected = compute_content_hash(twin)
        if twin.content_hash != expected:
            errors.append(
                f"content_hash mismatch: stored={twin.content_hash} expected={expected}"
            )
    if errors:
        raise ValueError(
            "GoldenTwin validation failed:\n  - " + "\n  - ".join(errors)
        )


def load_golden_twin(path: Path) -> GoldenTwin:
    raw = json.loads(Path(path).read_text())
    twin = GoldenTwin.model_validate(raw)
    validate_golden_twin(twin)
    return twin


def save_golden_twin(twin: GoldenTwin, path: Path) -> None:
    """Canonical serialisation + recomputed content_hash.

    Sorts entity rows by `id` (or by the natural-key tuple for edge tables) so
    the JSON output is byte-stable for a given input.
    """
    twin = twin.model_copy(deep=True)
    twin.content_hash = compute_content_hash(twin)

    payload = twin.model_dump(mode="json", by_alias=True)
    for table, rows in payload["entities"].items():
        if not isinstance(rows, list):
            continue
        if table == "Interface_Link":
            rows.sort(key=lambda r: (r["interface_id"], r["link_id"]))
        elif table == "Subnet_Association":
            rows.sort(key=lambda r: (r["entity_id"], r["subnet_id"]))
        else:
            rows.sort(key=lambda r: r["id"])
        payload["entities"][table] = rows

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")


def summarise_counts(twin: GoldenTwin) -> dict[str, int]:
    return {table: len(rows) for table, rows in twin.entities.tables()}
