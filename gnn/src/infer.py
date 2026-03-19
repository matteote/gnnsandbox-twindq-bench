# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""
GNN Inference — Cloud Run Job entry point

Stateless inference process triggered by Cloud Scheduler every 60 seconds.

Replaces the background_inference_loop() thread that previously ran inside
serve.py.  This process:
  1. Reads latest_run.json from GCS to find the current best model artefacts
  2. Downloads model weights if not already cached in /tmp
  3. Fetches the latest network snapshot from Spanner
  4. Runs HetGNN inference (embeddings + anomaly scoring)
  5. Writes results back to Spanner
  6. Exits with code 0 (success) or 1 (error)

Cloud Run Jobs handle retry logic (max 2 retries) and billing is per-execution.
"""

import asyncio
import json
import logging
import os
import pickle
import sys
import tempfile
from pathlib import Path

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("gnn.infer")

# ── Environment ───────────────────────────────────────────────────────────────
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "network-model-artifacts")
SPANNER_INSTANCE = os.getenv("SPANNER_INSTANCE", "networktopology-instance")
SPANNER_DATABASE = os.getenv("SPANNER_DATABASE", "networktopology-db")
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
CACHE_DIR = Path(tempfile.gettempdir()) / "gnn_model_cache"

# ── Model state (module-level, reused if container is warm) ───────────────────
_models: dict = {}
_scalers: dict = {}
_run_id: str = ""


def _gcs_client():
    from google.cloud import storage
    return storage.Client()


def _spanner_client():
    from google.cloud import spanner as gspanner
    return gspanner.Client()


def load_manifest() -> dict:
    """Read latest_run.json from GCS to find current best model paths."""
    client = _gcs_client()
    bucket = client.bucket(GCS_BUCKET_NAME)
    blob = bucket.blob("models/latest/latest_run.json")
    if not blob.exists():
        raise FileNotFoundError(
            f"gs://{GCS_BUCKET_NAME}/models/latest/latest_run.json not found. "
            "Run the training pipeline first."
        )
    return json.loads(blob.download_as_text())


def download_model_artefacts(manifest: dict) -> dict:
    """Download model .pth files from GCS to local cache if not already present.

    Returns a dict of {model_name: local_path}.
    """
    global _run_id
    run_id = manifest["run_id"]
    local_paths = {}

    if run_id == _run_id and _models:
        logger.info(f"Model cache is current (run_id={run_id}), skipping download")
        return {}

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    client = _gcs_client()
    bucket = client.bucket(GCS_BUCKET_NAME)

    for model_name, model_info in manifest.get("models", {}).items():
        gcs_path = model_info["gcs_path"]
        # Strip gs://bucket/ prefix to get blob prefix
        prefix = gcs_path.replace(f"gs://{GCS_BUCKET_NAME}/", "")

        local_dir = CACHE_DIR / model_name / run_id
        local_dir.mkdir(parents=True, exist_ok=True)

        for blob in bucket.list_blobs(prefix=prefix):
            filename = Path(blob.name).name
            if filename.endswith(".pth") or filename.endswith(".pkl"):
                local_path = local_dir / filename
                if not local_path.exists():
                    logger.info(f"Downloading {blob.name} → {local_path}")
                    blob.download_to_filename(str(local_path))
                local_paths[f"{model_name}/{filename}"] = str(local_path)

    _run_id = run_id
    return local_paths


def load_models(manifest: dict, local_paths: dict):
    """Load PyTorch model weights into the module-level _models dict."""
    global _models, _scalers

    import torch
    from model.hetgnn import HetGNN

    run_id = manifest["run_id"]
    local_dir = CACHE_DIR / "hetgnn" / run_id

    # Load HetGNN (primary model for scheduled inference)
    model_pth = local_dir / "model.pth"
    stats_pth = local_dir / "model_stats.pth"

    if not model_pth.exists():
        raise FileNotFoundError(f"HetGNN model not found at {model_pth}")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    checkpoint = torch.load(str(model_pth), map_location=device)

    # Reconstruct model from saved hyperparams (must match training config)
    model = HetGNN(
        hidden_channels=checkpoint.get("hidden_channels", 64),
        num_layers=checkpoint.get("num_layers", 2),
    )
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()
    model.to(device)

    _models["hetgnn"] = model
    logger.info(f"Loaded HetGNN model from {model_pth}")

    # Load scalers
    scalers_dir = CACHE_DIR / "hetgnn" / run_id
    scalers_pth = scalers_dir / "scalers.pkl"
    if scalers_pth.exists():
        with open(str(scalers_pth), "rb") as f:
            _scalers = pickle.load(f)
        logger.info("Loaded scalers from cache")


def fetch_latest_snapshot() -> dict:
    """Fetch the most recent network topology snapshot from Spanner."""
    from google.cloud import spanner as gspanner

    spanner_client = _spanner_client()
    instance = spanner_client.instance(SPANNER_INSTANCE)
    database = instance.database(SPANNER_DATABASE)

    with database.snapshot() as snap:
        results = snap.execute_sql(
            "SELECT SnapshotId, Timestamp, TopologyData "
            "FROM TopologySnapshots "
            "ORDER BY Timestamp DESC LIMIT 1"
        )
        row = next(iter(results), None)
        if row is None:
            raise RuntimeError("No snapshots found in Spanner TopologySnapshots table")
        snapshot_id, timestamp, topology_data = row
        return {
            "snapshot_id": str(snapshot_id),
            "timestamp": str(timestamp),
            "topology": topology_data,
        }


async def run_inference(snapshot: dict) -> dict:
    """Run HetGNN inference on the given snapshot and return results."""
    import torch
    from utils.gnn_utils import build_graph_from_snapshot

    if "hetgnn" not in _models:
        raise RuntimeError("Models not loaded — call load_models() first")

    device = next(_models["hetgnn"].parameters()).device
    data = build_graph_from_snapshot(snapshot, scalers=_scalers)
    data = data.to(device)

    with torch.no_grad():
        embeddings, anomaly_scores = _models["hetgnn"](data)

    return {
        "snapshot_id": snapshot["snapshot_id"],
        "timestamp": snapshot["timestamp"],
        "embeddings": embeddings.cpu().numpy().tolist(),
        "anomaly_scores": anomaly_scores.cpu().numpy().tolist(),
        "anomaly_count": int((anomaly_scores > 0.5).sum().item()),
    }


def write_results_to_spanner(results: dict):
    """Persist inference results (embeddings + anomaly scores) to Spanner."""
    import json as _json
    from google.cloud import spanner as gspanner

    spanner_client = _spanner_client()
    instance = spanner_client.instance(SPANNER_INSTANCE)
    database = instance.database(SPANNER_DATABASE)

    def _write(transaction):
        transaction.insert_or_update(
            table="GNNInferenceResults",
            columns=["SnapshotId", "Timestamp", "Embeddings", "AnomalyScores", "AnomalyCount"],
            values=[[
                results["snapshot_id"],
                results["timestamp"],
                _json.dumps(results["embeddings"]),
                _json.dumps(results["anomaly_scores"]),
                results["anomaly_count"],
            ]],
        )

    database.run_in_transaction(_write)
    logger.info(
        f"Wrote inference results for snapshot {results['snapshot_id']} "
        f"({results['anomaly_count']} anomalies) to Spanner"
    )


def main():
    logger.info("GNN inference job starting...")

    try:
        # 1. Load manifest
        logger.info("Reading latest_run.json from GCS...")
        manifest = load_manifest()
        logger.info(f"Using model run_id: {manifest['run_id']}")

        # 2. Download model artefacts (cached in /tmp for warm container reuse)
        local_paths = download_model_artefacts(manifest)

        # 3. Load model weights
        load_models(manifest, local_paths)

        # 4. Fetch latest snapshot
        logger.info("Fetching latest snapshot from Spanner...")
        snapshot = fetch_latest_snapshot()
        logger.info(f"Got snapshot {snapshot['snapshot_id']} ({snapshot['timestamp']})")

        # 5. Run inference
        results = asyncio.run(run_inference(snapshot))
        logger.info(
            f"Inference complete: {results['anomaly_count']} anomalies detected"
        )

        # 6. Write results back to Spanner
        write_results_to_spanner(results)

        logger.info("GNN inference job completed successfully.")
        sys.exit(0)

    except Exception as exc:
        logger.exception(f"GNN inference job failed: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
