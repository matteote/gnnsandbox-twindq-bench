"""TwinDQ-Bench injector CLI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _cmd_snapshot(args: argparse.Namespace) -> int:
    from injector.loader import save_golden_twin

    if args.from_yaml:
        from injector.snapshot.from_yaml import snapshot_from_yaml

        twin = snapshot_from_yaml(Path(args.from_yaml))
    elif args.from_spanner:
        from injector.snapshot.from_spanner import snapshot_from_spanner

        twin = snapshot_from_spanner(
            project=args.project,
            instance=args.instance,
            database=args.database,
            network_name=args.network_name or args.database,
        )
    else:
        print("snapshot: must pass --from-yaml DIR or --from-spanner", file=sys.stderr)
        return 2

    save_golden_twin(twin, Path(args.out))
    print(f"wrote {args.out}")
    return 0


def _cmd_project(args: argparse.Namespace) -> int:
    from injector.loader import load_golden_twin
    from injector.projector.catalog import to_catalog
    from injector.projector.telemetry import to_telemetry

    twin = load_golden_twin(Path(args.golden))
    to_catalog(twin, Path(args.out_catalog), seed=args.seed)
    to_telemetry(twin, Path(args.out_telemetry))
    print(f"wrote catalog to {args.out_catalog} and telemetry to {args.out_telemetry}")
    return 0


def _cmd_inject(args: argparse.Namespace) -> int:
    from injector.scenario import load_scenario, run_scenario

    scenario = load_scenario(Path(args.scenario))
    run_scenario(scenario, overwrite=args.overwrite)
    print(f"scenario {scenario.name} complete")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    from injector.loader import load_golden_twin, summarise_counts

    twin = load_golden_twin(Path(args.golden))
    counts = summarise_counts(twin)
    print(f"Golden Twin OK ({twin.source.network_name}, hash={twin.content_hash})")
    for table, n in counts.items():
        print(f"  {table:24s} {n:>5d}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="benchmark-injector",
        description="TwinDQ-Bench injector — generate labelled data-quality defects.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("snapshot", help="Build a Golden Twin")
    src = sp.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--from-yaml",
        metavar="DIR",
        help="Directory of VyOSInfrastructure / VyOSL3VPN YAML files",
    )
    src.add_argument(
        "--from-spanner", action="store_true", help="Dump from a live Spanner database"
    )
    sp.add_argument("--project")
    sp.add_argument("--instance")
    sp.add_argument("--database")
    sp.add_argument("--network-name")
    sp.add_argument("--out", required=True, help="Output GoldenTwin JSON path")
    sp.set_defaults(func=_cmd_snapshot)

    pp = sub.add_parser("project", help="Project a Golden Twin (debug)")
    pp.add_argument("--golden", required=True)
    pp.add_argument("--out-catalog", required=True)
    pp.add_argument("--out-telemetry", required=True)
    pp.add_argument("--seed", type=int, default=0)
    pp.set_defaults(func=_cmd_project)

    ip = sub.add_parser("inject", help="Run a scenario end-to-end")
    ip.add_argument("--scenario", required=True)
    ip.add_argument("--overwrite", action="store_true")
    ip.set_defaults(func=_cmd_inject)

    vp = sub.add_parser("validate", help="Validate a Golden Twin")
    vp.add_argument("--golden", required=True)
    vp.set_defaults(func=_cmd_validate)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
