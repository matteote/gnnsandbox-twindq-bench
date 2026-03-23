# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""
GNN Serving — Vertex AI Endpoint

HTTP prediction server deployed to Vertex AI after each successful training
pipeline run.  Triggered periodically by Cloud Scheduler AND available for
on-demand queries from network agents / the dashboard.

Each POST /predict request:
  1. Loads model weights + scalers from GCS (cached in /tmp across requests;
     reloads automatically when a new training run updates latest_run.json)
  2. Fetches the latest network snapshot from Spanner
  3. Runs HetGNN inference → per-node embeddings + MSE reconstruction scores
  4. Writes results to the Spanner NodeEmbedding table
  5. Returns a JSON summary of anomalies detected

GET /health returns {"status": "healthy"}.
"""

import json
import logging
import os
import sys
import tempfile
import uuid
from pathlib import Path

from aiohttp import web
import aiohttp_cors

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("gnn.serve")

# ── Environment ───────────────────────────────────────────────────────────────
GCS_BUCKET_NAME      = os.getenv("GCS_BUCKET_NAME",     "network-model-artifacts")
SPANNER_INSTANCE     = os.getenv("SPANNER_INSTANCE",     "networktopology-instance")
SPANNER_DATABASE     = os.getenv("SPANNER_DATABASE",     "networktopology-db")
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", None)
ANOMALY_THRESHOLD    = float(os.getenv("ANOMALY_THRESHOLD", "0.5"))
CACHE_DIR            = Path(tempfile.gettempdir()) / "gnn_model_cache"

# ── In-process model cache (warm across concurrent requests) ──────────────────
_cache: dict = {}


# ── GCS helpers ───────────────────────────────────────────────────────────────

def _gcs_client():
    from google.cloud import storage
    return storage.Client()


def _load_manifest() -> dict:
    """Read latest_run.json to find the current best model paths."""
    client = _gcs_client()
    bucket = client.bucket(GCS_BUCKET_NAME)
    blob = bucket.blob("models/latest/latest_run.json")
    if not blob.exists():
        raise FileNotFoundError(
            f"gs://{GCS_BUCKET_NAME}/models/latest/latest_run.json not found. "
            "Run the GNN training pipeline first."
        )
    return json.loads(blob.download_as_text())


def _download_artefacts(manifest: dict) -> Path:
    """Download model.pth and scalers.pkl for the current run into /tmp."""
    run_id = manifest["run_id"]
    local_dir = CACHE_DIR / "hetgnn" / run_id
    local_dir.mkdir(parents=True, exist_ok=True)

    client = _gcs_client()
    bucket = client.bucket(GCS_BUCKET_NAME)
    model_info = manifest.get("models", {}).get("hetgnn", {})
    gcs_path = model_info.get(
        "gcs_path",
        f"gs://{GCS_BUCKET_NAME}/models/hetgnn/{run_id}",
    )
    prefix = gcs_path.replace(f"gs://{GCS_BUCKET_NAME}/", "")

    for blob in bucket.list_blobs(prefix=prefix):
        filename = Path(blob.name).name
        if filename.endswith(".pth") or filename.endswith(".pkl"):
            local_path = local_dir / filename
            if not local_path.exists():
                logger.info(f"Downloading {blob.name} → {local_path}")
                blob.download_to_filename(str(local_path))

    return local_dir


# ── Model loader (lazy, cached, auto-refreshes on new run_id) ─────────────────

def _ensure_model_loaded(manifest: dict):
    """Load or refresh the HetGNN model + GraphBuilder into _cache."""
    import torch
    from model.hetgnn import HetGNN
    from utils.gnn_utils import GraphBuilder, INTERVAL_MINUTES
    from utils.data import SpannerDataset

    run_id = manifest["run_id"]
    if _cache.get("run_id") == run_id and _cache.get("model") is not None:
        return  # already loaded for this run

    logger.info(f"Loading model for run_id={run_id}...")
    local_dir = _download_artefacts(manifest)

    # Load scalers
    scalers_pth = local_dir / "scalers.pkl"
    if not scalers_pth.exists():
        raise FileNotFoundError(f"scalers.pkl not found at {scalers_pth}")
    graph_builder = GraphBuilder(scaler_path=str(scalers_pth))
    if not graph_builder.load_scalers():
        raise RuntimeError(f"Failed to load scalers from {scalers_pth}")

    # Fetch one snapshot to derive graph metadata
    dataset = SpannerDataset(
        instance_id=SPANNER_INSTANCE,
        database_id=SPANNER_DATABASE,
        num_snapshots=1,
        interval_minutes=INTERVAL_MINUTES,
        project_id=GOOGLE_CLOUD_PROJECT,
    )
    timestamps = dataset._get_timestamps()
    snapshot = dataset.fetch_snapshot(timestamps[-1])
    hetero_data, input_dims = graph_builder.process_snapshot(snapshot)
    metadata = hetero_data.metadata()

    # Load model weights
    model_pth = local_dir / "model.pth"
    if not model_pth.exists():
        raise FileNotFoundError(f"model.pth not found at {model_pth}")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    checkpoint = torch.load(str(model_pth), map_location=device, weights_only=False)

    model = HetGNN(
        metadata=metadata,
        hidden_channels=checkpoint.get("hidden_channels", 64),
        num_layers=checkpoint.get("num_layers", 2),
    )
    model.set_input_dims(input_dims)
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()
    model.to(device)

    _cache.update({
        "run_id":        run_id,
        "model":         model,
        "graph_builder": graph_builder,
        "dataset":       dataset,
        "device":        device,
    })
    logger.info(f"HetGNN model loaded (run_id={run_id}, device={device})")


# ── Spanner writer ────────────────────────────────────────────────────────────

def _write_results_to_spanner(database, graph_builder, snapshot: dict, results: dict):
    """Write one NodeEmbedding row per node (embeddings + anomaly scores)."""
    from google.cloud import spanner as gspanner

    reverse_id_maps = {
        ntype: {idx: nid for nid, idx in id_map.items()}
        for ntype, id_map in graph_builder.global_id_map.items()
    }
    node_names = {
        node["id"]: node.get("hostname", node.get("name", node["id"]))
        for node in snapshot.get("nodes", [])
    }

    rows = []
    for ntype, emb_list in results["embeddings"].items():
        scores  = results["anomaly_scores"].get(ntype, [])
        rev_map = reverse_id_maps.get(ntype, {})
        for idx, embedding in enumerate(emb_list):
            node_id = rev_map.get(idx)
            if node_id is None:
                continue
            score = float(scores[idx]) if idx < len(scores) else 0.0
            rows.append((
                str(uuid.uuid4()),
                node_id,
                ntype,
                embedding,
                score,
                json.dumps({"name": node_names.get(node_id, node_id)}),
                gspanner.COMMIT_TIMESTAMP,
            ))

    def _write(transaction):
        transaction.insert_or_update(
            table="NodeEmbedding",
            columns=[
                "id", "node_id", "node_type",
                "hetgnn_embedding", "hetgnn_score",
                "anomaly_explanation", "timestamp",
            ],
            values=rows,
        )

    database.run_in_transaction(_write)
    logger.info(f"Wrote {len(rows)} NodeEmbedding rows to Spanner")


# ── Core inference ────────────────────────────────────────────────────────────

async def _run_inference() -> dict:
    """Run one HetGNN inference cycle and return a result summary dict."""
    import torch
    from utils.gnn_utils import INTERVAL_MINUTES
    from utils.data import SpannerDataset

    manifest = _load_manifest()
    _ensure_model_loaded(manifest)

    model         = _cache["model"]
    graph_builder = _cache["graph_builder"]
    device        = _cache["device"]

    # Fetch the latest snapshot
    dataset = SpannerDataset(
        instance_id=SPANNER_INSTANCE,
        database_id=SPANNER_DATABASE,
        num_snapshots=1,
        interval_minutes=INTERVAL_MINUTES,
        project_id=GOOGLE_CLOUD_PROJECT,
    )
    timestamps = dataset._get_timestamps()
    latest_ts  = timestamps[-1]
    snapshot   = dataset.fetch_snapshot(latest_ts)
    logger.info(f"Fetched snapshot at {latest_ts.isoformat()}")

    hetero_data, _ = graph_builder.process_snapshot(snapshot)
    hetero_data = hetero_data.to(device)

    with torch.no_grad():
        recon_dict, out_embeddings = model(
            hetero_data.x_dict, hetero_data.edge_index_dict
        )

    # MSE reconstruction error → anomaly score
    anomaly_scores: dict = {}
    total_anomalies = 0
    for ntype, recon in recon_dict.items():
        orig = hetero_data[ntype].x
        mse  = ((recon - orig) ** 2).mean(dim=1)
        anomaly_scores[ntype] = mse.cpu().tolist()
        count = int((mse > ANOMALY_THRESHOLD).sum().item())
        total_anomalies += count
        logger.info(
            f"  {ntype}: {len(mse)} nodes, {count} anomalies "
            f"(MSE > {ANOMALY_THRESHOLD})"
        )

    embeddings = {
        ntype: emb.cpu().tolist()
        for ntype, emb in out_embeddings.items()
    }

    results = {
        "snapshot_timestamp": latest_ts.isoformat(),
        "embeddings":         embeddings,
        "anomaly_scores":     anomaly_scores,
        "anomaly_count":      total_anomalies,
    }

    _write_results_to_spanner(dataset.database, graph_builder, snapshot, results)

    logger.info(f"Inference complete — {total_anomalies} anomalies detected.")
    return results


# ── HTTP handlers ─────────────────────────────────────────────────────────────

async def predict_handler(request: web.Request) -> web.Response:
    logger.info("POST /predict")
    try:
        results = await _run_inference()
        return web.json_response({
            "predictions": [{
                "snapshot_timestamp": results["snapshot_timestamp"],
                "anomaly_count":      results["anomaly_count"],
                "anomaly_scores":     results["anomaly_scores"],
            }]
        })
    except Exception as exc:
        logger.exception(f"Inference failed: {exc}")
        return web.json_response({"error": str(exc)}, status=500)


async def health_handler(request: web.Request) -> web.Response:
    return web.json_response({"status": "healthy"})


# ── App setup ─────────────────────────────────────────────────────────────────

app = web.Application()
cors = aiohttp_cors.setup(app, defaults={
    "*": aiohttp_cors.ResourceOptions(
        allow_credentials=True,
        expose_headers="*",
        allow_headers="*",
    )
})

predict_route_path = os.environ.get("AIP_PREDICT_ROUTE", "/predict")
health_route_path  = os.environ.get("AIP_HEALTH_ROUTE",  "/health")

predict_route = app.router.add_post(predict_route_path, predict_handler)
health_route  = app.router.add_get(health_route_path,  health_handler)
cors.add(predict_route)
cors.add(health_route)

if __name__ == "__main__":
    port = int(os.environ.get("AIP_HTTP_PORT", 8080))
    logger.info(f"Starting GNN serving endpoint on :{port}")
    web.run_app(app, port=port)
