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
# Per-type thresholds are loaded from model_stats.pth at runtime;
# these are only the hard fallback defaults (matches ANOMALY_THRESHOLDS in gnn_utils).
_FALLBACK_THRESHOLDS = {
    "router":      0.15,
    "interface":   0.20,
    "bgp_session": 0.10,
    "vrf":         0.10,
    "flow":        0.15,
}
INTERVAL_MINUTES     = float(os.getenv("INTERVAL_MINUTES", "5"))
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
    import joblib
    import torch
    from model.hetgnn import HetGNN
    from utils.gnn_utils import GraphBuilder, FEATURE_DIMS, ANOMALY_THRESHOLDS
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
    scalers = joblib.load(str(scalers_pth))
    graph_builder = GraphBuilder(scalers=scalers)

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
    hetero_data = graph_builder.process_snapshot(snapshot)
    metadata = hetero_data.metadata()
    input_dims = FEATURE_DIMS

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

    # Load per-type anomaly thresholds from model_stats (fall back to defaults)
    thresholds = dict(ANOMALY_THRESHOLDS)
    stats_pth = local_dir / "model_stats.pth"
    if stats_pth.exists():
        try:
            stats = torch.load(str(stats_pth), map_location="cpu", weights_only=False)
            thresholds.update(stats.get("anomaly_thresholds", {}))
            logger.info(f"Loaded per-type thresholds from model_stats: {thresholds}")
        except Exception as exc:
            logger.warning(f"Could not load model_stats.pth: {exc}")

    _cache.update({
        "run_id":        run_id,
        "model":         model,
        "graph_builder": graph_builder,
        "dataset":       dataset,
        "device":        device,
        "thresholds":    thresholds,
    })
    logger.info(f"HetGNN model loaded (run_id={run_id}, device={device})")


# ── Spanner writer ────────────────────────────────────────────────────────────

def _write_results_to_spanner(database, node_id_map: dict, snapshot: dict, results: dict):
    """Write one NodeEmbedding row per node (embeddings + anomaly scores + explanation)."""
    from google.cloud import spanner as gspanner

    node_names = {
        node["id"]: node.get("hostname", node.get("name", node["id"]))
        for node in snapshot.get("nodes", [])
    }

    rows = []
    for ntype, emb_list in results["embeddings"].items():
        scores      = results["anomaly_scores"].get(ntype, [])
        explanations = results.get("anomaly_explanations", {}).get(ntype, [])
        id_list     = node_id_map.get(ntype, [])
        for idx, embedding in enumerate(emb_list):
            node_id = id_list[idx] if idx < len(id_list) else None
            if node_id is None:
                continue
            score = float(scores[idx]) if idx < len(scores) else 0.0
            expl  = explanations[idx] if idx < len(explanations) else {}
            rows.append((
                str(uuid.uuid4()),
                node_id,
                ntype,
                embedding,
                score,
                json.dumps({"name": node_names.get(node_id, node_id), **expl}),
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
    from utils.gnn_utils import (
        ROUTER_FEATURES, INTERFACE_FEATURES, BGP_SESSION_FEATURES,
        VRF_FEATURES, FLOW_FEATURES,
    )
    from utils.data import SpannerDataset

    # Maps each node type to its ordered feature name list for anomaly explanations.
    # When a node is anomalous, the top-3 feature names with highest MSE are reported.
    FEATURE_NAMES = {
        "router":      ROUTER_FEATURES,
        "interface":   INTERFACE_FEATURES,
        "bgp_session": BGP_SESSION_FEATURES,
        "vrf":         VRF_FEATURES,
        "flow":        FLOW_FEATURES,
    }

    manifest = _load_manifest()
    _ensure_model_loaded(manifest)

    model         = _cache["model"]
    graph_builder = _cache["graph_builder"]
    device        = _cache["device"]
    thresholds    = _cache.get("thresholds", _FALLBACK_THRESHOLDS)

    # Fetch the two most-recent snapshots so that temporal gradient / delta
    # features (rx_err_gradient, prefix_count_delta, vrf_route_count_delta,
    # throughput_delta) can be computed via compute_temporal_features().
    # Only the latest snapshot is passed to the model; the earlier one is only
    # used as the "previous" reference for finite-difference computation.
    dataset = SpannerDataset(
        instance_id=SPANNER_INSTANCE,
        database_id=SPANNER_DATABASE,
        num_snapshots=2,
        interval_minutes=INTERVAL_MINUTES,
        project_id=GOOGLE_CLOUD_PROJECT,
    )
    timestamps = dataset._get_timestamps()
    pair = [dataset.fetch_snapshot(ts) for ts in timestamps[-2:]]
    SpannerDataset.compute_temporal_features(pair, interval_seconds=INTERVAL_MINUTES * 60)
    latest_ts = timestamps[-1]
    snapshot  = pair[-1]   # the second snapshot has its deltas populated
    logger.info(f"Fetched snapshot pair ending at {latest_ts.isoformat()} (temporal features computed)")

    hetero_data = graph_builder.process_snapshot(snapshot)
    node_id_map = hetero_data.node_id_map   # {ntype: [node_id, ...]}
    hetero_data = hetero_data.to(device)

    with torch.no_grad():
        recon_dict, out_embeddings = model(
            hetero_data.x_dict, hetero_data.edge_index_dict
        )

    # Per-type MSE anomaly scoring + per-feature explanations
    anomaly_scores: dict = {}
    anomaly_explanations: dict = {}
    total_anomalies = 0

    for ntype, recon in recon_dict.items():
        if ntype not in hetero_data.x_dict:
            continue
        orig      = hetero_data[ntype].x
        feat_mse  = ((recon - orig) ** 2)           # [N, F]
        node_mse  = feat_mse.mean(dim=1)             # [N]
        threshold = thresholds.get(ntype, 0.20)
        is_anomaly = node_mse > threshold

        anomaly_scores[ntype] = node_mse.cpu().tolist()
        count = int(is_anomaly.sum().item())
        total_anomalies += count
        logger.info(
            f"  {ntype}: {len(node_mse)} nodes, {count} anomalies "
            f"(MSE > {threshold})"
        )

        # Per-feature explanation for anomalous nodes
        feat_names = FEATURE_NAMES.get(ntype, [])
        expl_list = []
        for node_idx in range(node_mse.size(0)):
            if is_anomaly[node_idx]:
                per_feat = feat_mse[node_idx].cpu().tolist()
                top_feat = sorted(
                    enumerate(per_feat), key=lambda x: x[1], reverse=True
                )[:3]
                expl_list.append({
                    "anomalous": True,
                    "top_features": [
                        {"feature": feat_names[fi] if fi < len(feat_names) else str(fi),
                         "mse": round(fv, 5)}
                        for fi, fv in top_feat
                    ],
                })
            else:
                expl_list.append({"anomalous": False})
        anomaly_explanations[ntype] = expl_list

    embeddings = {
        ntype: emb.cpu().tolist()
        for ntype, emb in out_embeddings.items()
    }

    results = {
        "snapshot_timestamp":  latest_ts.isoformat(),
        "embeddings":          embeddings,
        "anomaly_scores":      anomaly_scores,
        "anomaly_explanations": anomaly_explanations,
        "anomaly_count":       total_anomalies,
    }

    _write_results_to_spanner(dataset.database, node_id_map, snapshot, results)

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
