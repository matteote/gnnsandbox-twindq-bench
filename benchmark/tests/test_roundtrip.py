"""Round-trip + hash-stability tests for the loader and projectors."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from injector.loader import compute_content_hash, load_golden_twin, save_golden_twin
from injector.projector.catalog import to_catalog
from injector.projector.telemetry import to_telemetry
from injector.snapshot.from_yaml import snapshot_from_yaml


REF_NETWORK_DIR = Path(__file__).resolve().parents[2] / "environment" / "telco-lab" / "l3vpn-network"


def test_golden_twin_content_hash_stable(tiny_twin) -> None:
    h1 = compute_content_hash(tiny_twin)
    h2 = compute_content_hash(tiny_twin)
    assert h1 == h2
    assert h1.startswith("sha256:")


def test_golden_twin_roundtrip(tmp_path: Path, tiny_twin) -> None:
    out = tmp_path / "twin.json"
    save_golden_twin(tiny_twin, out)
    reloaded = load_golden_twin(out)
    assert reloaded.content_hash == compute_content_hash(tiny_twin)
    # Counts preserved
    for table, rows in tiny_twin.entities.tables():
        assert len(reloaded.entities.table(table)) == len(rows)


def test_yaml_snapshot_produces_valid_twin() -> None:
    if not REF_NETWORK_DIR.is_dir():
        pytest.skip("reference network YAMLs not present")
    twin = snapshot_from_yaml(REF_NETWORK_DIR)
    # Acceptance count (plan §C.10): 18 routers, 8 devices, 3 services, 8 VRFs, 8 BGP.
    assert len(twin.entities.routers) == 18
    assert len(twin.entities.devices) == 8
    assert len(twin.entities.l3vpn_services) == 3
    assert len(twin.entities.vrfs) == 8
    assert len(twin.entities.bgp_sessions) == 8
    # Each p2p link must have exactly two Interface_Link rows.
    edges_per_link: dict[str, int] = {}
    for il in twin.entities.interface_link:
        edges_per_link[il.link_id] = edges_per_link.get(il.link_id, 0) + 1
    assert all(c == 2 for c in edges_per_link.values())


def test_projector_writes_expected_files(tmp_path: Path, tiny_twin) -> None:
    cat_dir = tmp_path / "catalog"
    tel_path = tmp_path / "telemetry.json"
    to_catalog(tiny_twin, cat_dir, seed=0)
    to_telemetry(tiny_twin, tel_path)
    for filename in ("routers.csv", "interfaces.csv", "links.csv", "devices.csv", "vpns.csv"):
        assert (cat_dir / filename).exists()
    payload = json.loads(tel_path.read_text())
    assert "PhysicalRouter" in payload
    # Telemetry drops location_city per §5.3
    assert "location_city" not in payload["PhysicalRouter"][0]


def test_projection_is_deterministic(tmp_path: Path, tiny_twin) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    to_catalog(tiny_twin, a, seed=123)
    to_catalog(tiny_twin, b, seed=123)
    for filename in ("routers.csv", "interfaces.csv", "links.csv", "devices.csv", "vpns.csv"):
        assert (a / filename).read_bytes() == (b / filename).read_bytes()
