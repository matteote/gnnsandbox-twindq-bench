"""Scenario YAML loader + end-to-end runner."""

from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path
from random import Random
from typing import Any

import yaml

from injector.defects.base import (
    CrossSourceDefectClass,
    DefectClass,
    DefectContext,
    Projection,
    build_projection,
    select_entities,
)
from injector.defects.registry import is_cross_source, resolve
from injector.ledger import LedgerWriter
from injector.loader import load_golden_twin
from injector.projector.catalog import to_catalog as _write_catalog_from_twin
from injector.projector.telemetry import (
    ATTR_ALLOW,
    build_telemetry_payload,
)
from injector.projector.catalog import (
    _bandwidth_to_mbps,
    _interface_description,
    _last_audit_date,
    _parse_speed_mbps,
    _synth_asset_tag,
    _synth_fiber_type,
    _synth_length_km,
    _synth_mgmt_ip,
    _synth_serial,
    to_display_name,
)
from injector.types import (
    DefectRecord,
    DefectSpec,
    EntityRef,
    GoldenTwin,
    Mutation,
    Scenario,
)


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


def load_scenario(path: Path) -> Scenario:
    raw = yaml.safe_load(Path(path).read_text())
    return Scenario.model_validate(raw)


# ---------------------------------------------------------------------------
# Rendering (post-defect)
# ---------------------------------------------------------------------------


def _render_telemetry_from_projection(
    projection: Projection, output_path: Path
) -> None:
    payload: dict[str, Any] = {}
    for table, rows in projection.tables.items():
        allow = ATTR_ALLOW[table]
        if table == "Interface_Link":
            key = lambda r: (r.get("interface_id"), r.get("link_id"))
        elif table == "Subnet_Association":
            key = lambda r: (r.get("entity_id"), r.get("subnet_id"))
        else:
            key = lambda r: r.get("id") or ""
        projected = [{k: r.get(k) for k in allow} for r in rows]
        projected.sort(key=key)
        payload[table] = projected
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")


def _render_catalog_from_projection(
    projection: Projection,
    output_dir: Path,
    seed: int,
    captured_at: str,
) -> None:
    """Render the catalog CSVs from (possibly mutated) entity rows.

    Inventory-only columns (inventory_id, serial_number, etc.) are synthesised
    deterministically from id+seed so that they remain stable for unaffected
    rows. Rows that the defect created have their inventory fields synthesised
    against their (possibly fabricated) id.
    """
    import pandas as pd

    output_dir.mkdir(parents=True, exist_ok=True)
    routers = sorted(projection.tables["PhysicalRouter"], key=lambda r: r["id"])
    routers_rows = []
    for seq, r in enumerate(routers):
        routers_rows.append(
            {
                "inventory_id": f"INV-{seq + 1:04d}",
                "hostname": r.get("name"),
                "display_name": to_display_name(r.get("name") or ""),
                "vendor": r.get("vendor"),
                "model": r.get("model"),
                "serial_number": _synth_serial(seed, r.get("id") or ""),
                "asset_tag": _synth_asset_tag(seed, r.get("id") or ""),
                "site": r.get("location_city"),
                "latitude": r.get("location_lat"),
                "longitude": r.get("location_lon"),
                "role": r.get("role"),
                "mgmt_ip": _synth_mgmt_ip(seq),
                "last_audit_date": _last_audit_date_from_iso(captured_at, seed, r.get("id") or ""),
            }
        )
    pd.DataFrame(routers_rows).to_csv(
        output_dir / "routers.csv", index=False, lineterminator="\n"
    )

    router_by_id = {r.get("id"): r for r in projection.tables["PhysicalRouter"]}
    iface_rows = []
    interfaces = sorted(projection.tables["PhysicalInterface"], key=lambda r: r["id"])
    for seq, i in enumerate(interfaces):
        router = router_by_id.get(i.get("router_id"))
        iface_rows.append(
            {
                "inventory_id": f"IF-{seq + 1:04d}",
                "device_hostname": router.get("name") if router else None,
                "interface_name": i.get("name"),
                "speed_mbps": _parse_speed_mbps(i.get("speed")),
                "media_type": i.get("media_type"),
                "planned_ip": i.get("ip_address"),
                "mac_address": i.get("mac_address"),
                "description": _interface_description_from_dicts(i, router),
            }
        )
    pd.DataFrame(iface_rows).to_csv(
        output_dir / "interfaces.csv", index=False, lineterminator="\n"
    )

    iface_by_id = {i.get("id"): i for i in projection.tables["PhysicalInterface"]}
    endpoints: dict[str, list[str]] = {}
    for il in projection.tables["Interface_Link"]:
        endpoints.setdefault(il.get("link_id"), []).append(il.get("interface_id"))
    link_rows = []
    links = sorted(projection.tables["PhysicalLink"], key=lambda r: r["id"])
    for seq, link in enumerate(links):
        ep_ids = sorted(endpoints.get(link["id"], []))
        ep = []
        for iid in ep_ids[:2]:
            iface = iface_by_id.get(iid)
            router = router_by_id.get(iface.get("router_id")) if iface else None
            ep.append((router.get("name") if router else None, iface.get("name") if iface else None))
        while len(ep) < 2:
            ep.append((None, None))
        link_rows.append(
            {
                "inventory_id": f"LINK-{seq + 1:04d}",
                "circuit_id": f"CKT-{link.get('name')}",
                "endpoint_a_device": ep[0][0],
                "endpoint_a_interface": ep[0][1],
                "endpoint_b_device": ep[1][0],
                "endpoint_b_interface": ep[1][1],
                "bandwidth_mbps": _bandwidth_to_mbps(link.get("bandwidth")),
                "fiber_type": _synth_fiber_type(link.get("name") or ""),
                "length_km": _synth_length_km(link.get("name") or ""),
            }
        )
    pd.DataFrame(link_rows).to_csv(
        output_dir / "links.csv", index=False, lineterminator="\n"
    )

    device_rows = []
    for d in sorted(projection.tables["Device"], key=lambda r: r["id"]):
        iface = iface_by_id.get(d.get("interface_id")) if d.get("interface_id") else None
        router = router_by_id.get(iface.get("router_id")) if iface else None
        device_rows.append(
            {
                "inventory_id": d.get("id"),
                "device_name": d.get("name"),
                "network_name": d.get("network_name"),
                "ip_address": d.get("ip_address"),
                "mgmt_ip": d.get("mgmt_ip"),
                "gateway": d.get("gateway"),
                "gateway_router": router.get("name") if router else None,
                "gateway_interface": iface.get("name") if iface else None,
                "vlan": d.get("vlan"),
            }
        )
    pd.DataFrame(device_rows).to_csv(
        output_dir / "devices.csv", index=False, lineterminator="\n"
    )

    services_by_id = {s.get("id"): s for s in projection.tables["L3VPNService"]}
    vpn_rows = []
    for vrf in sorted(projection.tables["VRF"], key=lambda r: r["id"]):
        svc = services_by_id.get(vrf.get("vpn_id"))
        vpn_rows.append(
            {
                "vpn_id": vrf.get("vpn_id"),
                "vpn_name": svc.get("name") if svc else None,
                "service_type": svc.get("service_type") if svc else None,
                "topology": svc.get("topology") if svc else None,
                "vrf_id": vrf.get("id"),
                "vrf_name": vrf.get("name"),
                "router_id": vrf.get("router_id"),
                "rd": vrf.get("rd"),
                "status": vrf.get("status"),
            }
        )
    pd.DataFrame(vpn_rows).to_csv(
        output_dir / "vpns.csv", index=False, lineterminator="\n"
    )


def _last_audit_date_from_iso(captured_at: str, seed: int, router_id: str) -> str:
    import datetime as _dt
    import hashlib

    base = _dt.datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    base_date = (base - _dt.timedelta(days=30)).date()
    digest = hashlib.sha256(f"{seed}:audit:{router_id}".encode()).digest()
    jitter = (int.from_bytes(digest[:4], "big") % 11) - 5
    return (base_date + _dt.timedelta(days=jitter)).isoformat()


def _interface_description_from_dicts(iface: dict, router: dict | None) -> str:
    if router is None:
        return f"Interface {iface.get('name')}"
    role = router.get("role") or "?"
    return f"{role} {router.get('name')} :: {iface.get('name')}"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _apply_defect(
    spec: DefectSpec,
    scenario: Scenario,
    catalog: Projection,
    telemetry: Projection,
    ledger: LedgerWriter,
    counter: list[int],
    captured_at: str,
) -> None:
    cls_instance = resolve(spec.class_)
    # Per-defect RNG (plan §8.10) — independent of order.
    rng = Random(f"{scenario.seed}:{spec.id}")
    ctx = DefectContext(
        rng=rng,
        defect_spec_id=spec.id,
        scenario_name=scenario.name,
        seed=scenario.seed,
    )

    if isinstance(cls_instance, CrossSourceDefectClass):
        candidates = cls_instance.applicable_entities(
            catalog, telemetry, spec.parameters, spec.target_entity_type
        )
    else:
        target_projection = catalog if spec.target_projection == "catalog" else telemetry
        candidates = cls_instance.applicable_entities(
            target_projection, spec.parameters, spec.target_entity_type
        )

    selected = select_entities(
        candidates,
        spec.selection,
        rng,
        projection=(catalog if spec.target_projection == "catalog" else telemetry)
        if not isinstance(cls_instance, CrossSourceDefectClass)
        else None,
    )

    for entity in selected:
        if isinstance(cls_instance, CrossSourceDefectClass):
            mutation = cls_instance.apply(catalog, telemetry, entity, ctx, spec.parameters)
        else:
            target_projection = (
                catalog if spec.target_projection == "catalog" else telemetry
            )
            mutation = cls_instance.apply(
                target_projection, entity, ctx, spec.parameters
            )
        if mutation.kind == "noop":
            continue
        counter[0] += 1
        record = DefectRecord(
            defect_id=f"d-{counter[0]:04d}",
            scenario=scenario.name,
            seed=scenario.seed,
            defect_class=spec.class_,
            iso_dimension=cls_instance.iso_dimension,
            sid_entity_type=(
                spec.target_entity_type
                or cls_instance.default_sid_entity_type
                or entity.table
            ),
            target_projection=spec.target_projection,
            target_entity=entity,
            mutation=mutation,
            applied_at=_deterministic_applied_at(captured_at, counter[0]),
        )
        ledger.write(record)


def _deterministic_applied_at(captured_at: str, counter: int) -> str:
    """Stable ISO-8601 UTC timestamp derived from captured_at + counter seconds.

    Keeps the ledger byte-identical between runs of the same scenario+seed.
    """
    base = _dt.datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    stamp = base + _dt.timedelta(seconds=counter)
    return stamp.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_scenario(
    scenario: Scenario,
    *,
    overwrite: bool = False,
    base_dir: Path | None = None,
) -> None:
    base_dir = Path(base_dir) if base_dir else Path.cwd()
    golden_path = (base_dir / scenario.inputs.golden_twin).resolve()
    twin = load_golden_twin(golden_path)

    catalog_proj = build_projection("catalog", twin)
    telemetry_proj = build_projection("telemetry", twin)

    ledger_path = (base_dir / scenario.outputs.ledger_path).resolve()
    catalog_dir = (base_dir / scenario.outputs.catalog_dir).resolve()
    telemetry_path = (base_dir / scenario.outputs.telemetry_path).resolve()

    counter = [0]
    with LedgerWriter(ledger_path, overwrite=overwrite) as ledger:
        for spec in scenario.defects:
            _apply_defect(
                spec,
                scenario,
                catalog_proj,
                telemetry_proj,
                ledger,
                counter,
                twin.captured_at,
            )

    _render_catalog_from_projection(
        catalog_proj, catalog_dir, scenario.seed, twin.captured_at
    )
    _render_telemetry_from_projection(telemetry_proj, telemetry_path)
