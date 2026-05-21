# TwinDQ-Bench Injector (v1)

Generates labelled data-quality defects against a digital-twin network model,
for evaluating a downstream system that detects and flags such defects.

Three artefacts come out of one run:

| Artefact              | Consumer                 | Purpose                                    |
| --------------------- | ------------------------ | ------------------------------------------ |
| `catalog/*.csv`       | system under test        | corrupted "as-designed" inventory CSVs     |
| `telemetry.json`      | system under test        | corrupted "as-running" Spanner-shaped feed |
| `defect_ledger.jsonl` | benchmark evaluator (v2) | ground-truth answer key                    |

The defect vocabulary is aligned with ISO 8000-8 / DAMA-DMBOK data quality
dimensions: completeness, accuracy, consistency, uniqueness, validity. v1
covers topology only — counter / performance defects are v2.

## Install

```bash
cd benchmark
pip install -e .
# or with the Spanner snapshot extra:
pip install -e '.[spanner,dev]'
```

If you cannot install, prepend `PYTHONPATH=benchmark` to each `python3 -m
injector.cli …` invocation.

## CLI

```bash
benchmark-injector snapshot --from-yaml <network-dir> --out <golden.json>
benchmark-injector snapshot --from-spanner --project X --instance Y --database Z --out <golden.json>
benchmark-injector project --golden <golden.json> --out-catalog <dir> --out-telemetry <file>
benchmark-injector inject --scenario <scenario.yaml> [--overwrite]
benchmark-injector validate --golden <golden.json>
```

### End-to-end example (reference l3vpn network)

```bash
benchmark-injector snapshot \
  --from-yaml ../environment/telco-lab/l3vpn-network/ \
  --out examples/golden_twin/l3vpn-hub-spoke.json
benchmark-injector validate --golden examples/golden_twin/l3vpn-hub-spoke.json
benchmark-injector inject --scenario scenarios/chaos.yaml --overwrite
```

After `inject`, the `out/chaos/` tree contains the corrupted catalog CSVs, the
corrupted telemetry JSON, and the defect ledger.

## Subcommands in detail

- **`snapshot`** — build a Golden Twin from VyOSInfrastructure / VyOSL3VPN
  YAMLs (Mode B, primary in v1) or from a live Spanner database (Mode A,
  requires `[spanner]` extra). Output is reusable across runs.
- **`project`** — debug aid; renders one clean catalog + telemetry projection
  with no defects. Not part of the normal benchmark flow; the `inject` command
  performs projection internally.
- **`inject`** — load Golden Twin, project to both views, apply defects per the
  scenario, write the three output artefacts.
- **`validate`** — reload a Golden Twin and check id conventions, referential
  integrity, link cardinality, and content_hash.

## Scenarios

Five presets ship in `scenarios/`:

| Scenario                 | Tests                                                 |
| ------------------------ | ----------------------------------------------------- |
| `stale_catalog.yaml`     | one-sided completeness + accuracy on the catalog      |
| `naming_drift.yaml`      | catalog entity resolution (split/merged identity)     |
| `topology_mismatch.yaml` | structural disagreement between catalog and telemetry |
| `phantom_devices.yaml`   | existence conflicts (extras in one side only)         |
| `chaos.yaml`             | every defect class active simultaneously              |

Each scenario specifies a seed and an ordered list of defect specs. Same scenario
plus same seed produces byte-identical output, including the ledger's
`applied_at` timestamps (derived from `captured_at` plus a counter, not
wallclock).

## Defect catalog (v1)

| Defect class                       | ISO dim                 |
| ---------------------------------- | ----------------------- |
| `completeness.missing_node`        | Completeness            |
| `completeness.missing_edge`        | Completeness            |
| `completeness.missing_attribute`   | Completeness            |
| `accuracy.attribute_drift`         | Accuracy                |
| `accuracy.endpoint_shift`          | Accuracy                |
| `consistency.asymmetric_link`      | Consistency             |
| `consistency.orphan_entity`        | Consistency             |
| `uniqueness.split_identity`        | Uniqueness              |
| `uniqueness.merged_identity`       | Uniqueness              |
| `validity.malformed_id`            | Validity                |
| `validity.out_of_range`            | Validity                |
| `validity.invalid_enum`            | Validity                |
| `cross_source.attribute_conflict`  | Accuracy                |
| `cross_source.structural_conflict` | Consistency             |
| `cross_source.existence_conflict`  | Completeness / Accuracy |
| `cross_source.phantom_entity`      | Accuracy                |
| `cross_source.shadow_entity`       | Completeness            |

## Determinism contract

- Every randomised choice flows through `random.Random(f"{seed}:{defect.id}")`,
  so per-defect RNG is independent of ordering.
- Catalog CSV synthesised columns (serial, asset tag, mgmt IP, etc.) derive
  from `seed` + entity id.
- `applied_at` ledger timestamps derive from `captured_at` + counter, not
  wallclock.

Two runs of the same scenario with the same seed produce byte-identical
output trees.

## Tests

```bash
pytest -q
```

30 tests cover loader/projector roundtrips, every defect class, and the
scenario runner's determinism and overwrite semantics.

## Out of scope (v2)

`NetworkMetrics`, counter wraparound, jitter, performance outliers,
`FaultEvent` injection, time-ordered injection for timeliness detection,
the evaluator/scorecard. See plan §13 for the deferred list.
