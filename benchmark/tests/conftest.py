"""Shared pytest fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the `injector` package importable in `pip install`-free runs.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from injector.loader import load_golden_twin  # noqa: E402
from injector.types import GoldenTwin  # noqa: E402


FIXTURE_PATH = ROOT / "tests" / "fixtures" / "tiny_golden_twin.json"
L3VPN_GOLDEN = ROOT / "examples" / "golden_twin" / "l3vpn-hub-spoke.json"


@pytest.fixture
def tiny_twin() -> GoldenTwin:
    return load_golden_twin(FIXTURE_PATH)


@pytest.fixture
def l3vpn_twin() -> GoldenTwin:
    if not L3VPN_GOLDEN.exists():
        pytest.skip(f"Golden twin {L3VPN_GOLDEN} not present")
    return load_golden_twin(L3VPN_GOLDEN)
