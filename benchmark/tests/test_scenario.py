from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from injector.scenario import load_scenario, run_scenario
from injector.types import Scenario


BENCH_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BENCH_ROOT.parent
SCENARIO_DIR = BENCH_ROOT / "scenarios"


def _patch_paths(scenario: Scenario, work_dir: Path, twin_path: Path) -> Scenario:
    """Repoint a packaged scenario at a tmp output dir + custom golden twin."""
    payload = scenario.model_dump(mode="json", by_alias=True)
    payload["inputs"]["golden_twin"] = str(twin_path)
    payload["outputs"] = {
        "root": str(work_dir),
        "catalog_dir": str(work_dir / "catalog"),
        "telemetry_path": str(work_dir / "telemetry.json"),
        "ledger_path": str(work_dir / "defect_ledger.jsonl"),
    }
    return Scenario.model_validate(payload)


@pytest.fixture
def packaged_chaos(tmp_path: Path) -> Scenario:
    src = SCENARIO_DIR / "chaos.yaml"
    if not src.exists():
        pytest.skip("packaged scenario missing")
    return load_scenario(src)


def test_scenario_determinism(tmp_path: Path, packaged_chaos, l3vpn_twin) -> None:
    twin_path = REPO_ROOT / "benchmark" / "examples" / "golden_twin" / "l3vpn-hub-spoke.json"
    work_a = tmp_path / "a"
    work_b = tmp_path / "b"
    s_a = _patch_paths(packaged_chaos, work_a, twin_path)
    s_b = _patch_paths(packaged_chaos, work_b, twin_path)
    run_scenario(s_a, overwrite=True)
    run_scenario(s_b, overwrite=True)

    # Ledgers byte-identical
    assert (work_a / "defect_ledger.jsonl").read_bytes() == (work_b / "defect_ledger.jsonl").read_bytes()
    # Telemetry byte-identical
    assert (work_a / "telemetry.json").read_bytes() == (work_b / "telemetry.json").read_bytes()
    # Catalog files byte-identical
    for name in ("routers.csv", "interfaces.csv", "links.csv", "devices.csv", "vpns.csv"):
        assert (work_a / "catalog" / name).read_bytes() == (work_b / "catalog" / name).read_bytes()


def test_scenario_independence_under_reordering(tmp_path: Path, l3vpn_twin) -> None:
    """Per-defect RNG seeding (plan §8.10): two defects targeting independent
    tables must produce the same mutations regardless of order.

    State-dependent interactions between defects targeting the same rows are
    expected (and acceptable) — this test isolates the RNG-independence claim.
    """
    twin_path = REPO_ROOT / "benchmark" / "examples" / "golden_twin" / "l3vpn-hub-spoke.json"
    payload_template = {
        "name": "indep_test",
        "seed": 9001,
        "inputs": {"golden_twin": str(twin_path)},
        "outputs": {
            "root": str(tmp_path / "X"),
            "catalog_dir": str(tmp_path / "X" / "catalog"),
            "telemetry_path": str(tmp_path / "X" / "telemetry.json"),
            "ledger_path": str(tmp_path / "X" / "defect_ledger.jsonl"),
        },
        "defects": [
            {
                "id": "drift_vendor",
                "class": "accuracy.attribute_drift",
                "target_projection": "catalog",
                "target_entity_type": "PhysicalRouter",
                "selection": {"mode": "random_rate", "rate": 0.5},
                "parameters": {"attribute": "vendor"},
            },
            {
                "id": "mac_null",
                "class": "completeness.missing_attribute",
                "target_projection": "telemetry",
                "target_entity_type": "PhysicalInterface",
                "selection": {"mode": "random_rate", "rate": 0.5},
                "parameters": {"attribute": "mac_address"},
            },
        ],
    }

    def make_payload(order, work):
        p = {**payload_template}
        p["defects"] = [payload_template["defects"][i] for i in order]
        p["outputs"] = {
            "root": str(work),
            "catalog_dir": str(work / "catalog"),
            "telemetry_path": str(work / "telemetry.json"),
            "ledger_path": str(work / "defect_ledger.jsonl"),
        }
        return Scenario.model_validate(p)

    work_a = tmp_path / "a"
    work_b = tmp_path / "b"
    run_scenario(make_payload([0, 1], work_a), overwrite=True)
    run_scenario(make_payload([1, 0], work_b), overwrite=True)

    # Both ledgers should describe the same set of mutations (defect_id and
    # applied_at vary with execution order, so normalise them out).
    import json

    def normalise(p: Path) -> set[str]:
        out = set()
        for line in p.read_text().splitlines():
            d = json.loads(line)
            d.pop("defect_id", None)
            d.pop("applied_at", None)
            out.add(json.dumps(d, sort_keys=True))
        return out

    assert normalise(work_a / "defect_ledger.jsonl") == normalise(work_b / "defect_ledger.jsonl")


def test_scenario_refuses_overwrite(tmp_path: Path, packaged_chaos, l3vpn_twin) -> None:
    twin_path = REPO_ROOT / "benchmark" / "examples" / "golden_twin" / "l3vpn-hub-spoke.json"
    s = _patch_paths(packaged_chaos, tmp_path / "w", twin_path)
    run_scenario(s, overwrite=True)
    with pytest.raises(FileExistsError):
        run_scenario(s, overwrite=False)
