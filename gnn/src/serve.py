import logging
import os
import sys
import json
import asyncio
import torch
import torch.nn as nn
import numpy as np
from aiohttp import web
import aiohttp_cors
from google.cloud import storage
from google.cloud import spanner
from utils.gnn_utils import SPANNER_INSTANCE, SPANNER_DATABASE, GCS_BUCKET_NAME, INTERVAL_MINUTES, GraphBuilder, HIDDEN_CHANNELS, OUT_CHANNELS, NUM_HEADS, NUM_LAYERS
from utils.data import SpannerDataset

# Enhanced logging configuration
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)
BASE_DIR = os.path.dirname(os.path.realpath(__file__))

from model.stgnn import STGNN
from model.dgat import DGAT
from model.hetgnn import HetGNN

# Configuration
MODELS = {
    "stgnn": {"class": STGNN, "file": "stgnn_model.pth", "scaler": "stgnn_scalers.pkl", "stats": "stgnn_model_stats.pth", "instance": None},
    "dgat":  {"class": DGAT,  "file": "dgat_model.pth",  "scaler": "dgat_scalers.pkl",  "stats": "dgat_model_stats.pth",  "instance": None},
    "hetgnn":{"class": HetGNN,"file": "hetgnn_model.pth","scaler": "hetgnn_scalers.pkl","stats": "hetgnn_model_stats.pth","instance": None}
}
# Use per-node-type adaptive thresholds instead of global threshold
ANOMALY_THRESHOLD_MULTIPLIER = 2.5  # Multiplier for std deviation-based threshold

# Shared dependencies
gb = None

# NOTE: Background inference loop removed — inference now runs as a
# stateless Cloud Run Job triggered by Cloud Scheduler every 60 seconds.
# See gnn/src/infer.py and Dockerfile.infer.cloudrun.

def download_blob(bucket_name, source_blob_name, destination_file_name):
    """Downloads a blob from the bucket."""
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(source_blob_name)
        if blob.exists():
            blob.download_to_filename(destination_file_name)
            logger.info(f"Blob {source_blob_name} downloaded to {destination_file_name}.")
            return True
        else:
            logger.warning(f"Blob {source_blob_name} does not exist in bucket {bucket_name}.")
            return False
    except Exception as e:
        logger.error(f"Failed to download {source_blob_name} from GCS: {e}")
        return False

def compute_mahalanobis_distance(embedding, cluster_stats):
    """
    Computes Mahalanobis distance from cluster center.
    If covariance matrix not available, uses normalized Euclidean distance.
    
    Args:
        embedding: torch tensor of shape [F]
        cluster_stats: dict with 'mean', 'std', and optionally 'cov'
    
    Returns: scalar distance (higher = more anomalous)
    """
    mean = cluster_stats['mean']
    std = cluster_stats['std']
    
    # Check if covariance matrix is available (cluster stats)
    if 'cov' in cluster_stats:
        cov = cluster_stats['cov']
        # Handle numerical issues
        cov_reg = cov + torch.eye(cov.size(0)) * 1e-6
        
        try:
            cov_inv = torch.linalg.inv(cov_reg)
            diff = embedding - mean
            # Mahalanobis: sqrt((x-μ)ᵀ Σ⁻¹ (x-μ))
            distance = torch.sqrt(torch.abs(diff @ cov_inv @ diff.T))
            return distance.item()
        except:
            # Fallback to normalized euclidean if inversion fails
            diff = embedding - mean
            return torch.norm(diff / (std + 1e-8)).item()
    else:
        # Per-node stats only have mean/std, use normalized Euclidean
        diff = embedding - mean
        return torch.norm(diff / (std + 1e-8)).item()

def compute_anomaly_scores(embeddings_dict, stats_dict, model_name):
    """
    Computes anomaly scores for all nodes using Mahalanobis distance.
    Uses per-node baselines when available, falls back to cluster stats.
    
    Returns: {node_type: {node_idx: score}}, {node_type: threshold}
    """
    scores = {}
    thresholds = {}
    node_stats = stats_dict.get('node_stats', {})
    
    for node_type, emb_tensor in embeddings_dict.items():
        if node_type not in stats_dict['cluster_stats']:
            logger.warning(f"No cluster stats for {node_type}, skipping")
            continue
        
        cluster_stats = stats_dict['cluster_stats'][node_type]
        scores[node_type] = {}
        
        # Use per-node statistics to compute scores and adaptive thresholds
        per_node_scores = []
        has_node_stats = node_type in node_stats
        
        for node_idx in range(emb_tensor.size(0)):
            embedding = emb_tensor[node_idx]
            
            # Try to use node-specific baseline first
            if has_node_stats and node_idx in node_stats[node_type]:
                # Compare to this node's own historical pattern
                personal_stats = node_stats[node_type][node_idx]
                distance = compute_mahalanobis_distance(embedding, personal_stats)
                per_node_scores.append(distance)
            else:
                # Fallback to cluster stats for new/rare nodes
                distance = compute_mahalanobis_distance(embedding, cluster_stats)
            
            scores[node_type][node_idx] = distance
        
        # Compute adaptive threshold based on observed scores during this inference
        # Use 95th percentile of healthy node baselines + margin
        if per_node_scores and len(per_node_scores) > 2:
            # If we have per-node scores, use their 95th percentile
            per_node_scores_sorted = sorted(per_node_scores)
            percentile_95_idx = int(len(per_node_scores_sorted) * 0.95)
            threshold_95 = per_node_scores_sorted[percentile_95_idx]
            # Add 20% margin for detection (reduced from 50% to be more sensitive)
            adaptive_threshold = threshold_95 * 1.2
            
            # Debug logging for Router_Config to see individual scores
            if node_type == "Router_Config":
                logger.info(f"DEBUG Router_Config scores: {[f'{s:.1f}' for s in per_node_scores_sorted]}")
                logger.info(f"DEBUG 95th percentile: {threshold_95:.2f}, threshold: {adaptive_threshold:.2f}")
        else:
            # Fallback to cluster-based threshold
            feature_std = cluster_stats['std']
            avg_feature_std = feature_std.mean().item()
            adaptive_threshold = max(3.0, avg_feature_std * ANOMALY_THRESHOLD_MULTIPLIER)
        
        thresholds[node_type] = adaptive_threshold
    
    return scores, thresholds

def compute_feature_attribution(model, x_dict, edge_index_dict, node_type, node_idx, cluster_stats):
    """
    Uses gradient-based attribution to identify which input features
    contribute most to the anomaly.
    
    Returns: numpy array of importance scores (same shape as input features)
    """
    model.eval()
    
    # Clone inputs and enable gradients
    x_dict_grad = {}
    for nt, x in x_dict.items():
        if nt == node_type:
            x_dict_grad[nt] = x.clone().requires_grad_(True)
        else:
            x_dict_grad[nt] = x.clone()
    
    # Forward pass
    recon_dict, embeddings = model(x_dict_grad, edge_index_dict)
    
    # Compute distance to cluster for target node
    target_embedding = embeddings[node_type][node_idx]
    distance = compute_mahalanobis_distance(target_embedding, cluster_stats)
    distance_tensor = torch.tensor(distance, requires_grad=False)
    
    # Create a scalar that requires grad for backward
    loss = (target_embedding - cluster_stats['mean']).pow(2).sum()
    
    # Backward pass
    loss.backward()
    
    # Extract gradients for target node
    if x_dict_grad[node_type].grad is not None:
        grad = x_dict_grad[node_type].grad[node_idx]
        input_features = x_dict[node_type][node_idx]
        
        # Importance = |gradient| × |input| (sensitivity × magnitude)
        importance = (grad.abs() * input_features.abs()).detach().cpu().numpy()
    else:
        # No gradients available
        importance = np.zeros(x_dict[node_type].size(1))
    
    return importance

def map_to_spanner_node_type(internal_type):
    """
    Maps internal GNN node types to Spanner schema node types.
    
    Args:
        internal_type: GNN model node type (e.g., "PE Router", "Interface")
    
    Returns: Spanner schema node type ("PhysicalRouter" or "PhysicalInterface")
    """
    router_types = ["PE Router", "P Router", "CE Router", "Router_Config", "Protocol_State"]
    interface_types = ["Interface", "Interface_Metrics"]
    
    if internal_type in router_types:
        return "PhysicalRouter"
    elif internal_type in interface_types:
        return "PhysicalInterface"
    else:
        # Default fallback
        return internal_type

def explain_anomaly(importance, node_type, gb):
    """
    Translates feature importance scores into human-readable explanation.
    """
    top_k = 3
    top_indices = np.argsort(importance)[-top_k:][::-1]
    
    if node_type == "Router_Config":
        return f"Config anomaly: hash buckets {top_indices.tolist()}"
    
    elif node_type == "Interface":
        feature_names = ["state", "rx_bytes", "tx_bytes", "rx_drops", 
                        "tx_drops", "rx_errors", "tx_errors"]
        explanations = []
        for idx in top_indices:
            if idx < len(feature_names):
                explanations.append(feature_names[idx])
        return f"Interface anomaly: {', '.join(explanations)}"
    
    elif node_type == "Interface_Metrics":
        feature_names = ["rx_bytes", "tx_bytes", "rx_drops", "tx_drops", 
                        "rx_errors", "tx_errors",
                        "rx_bytes_velocity", "tx_bytes_velocity", 
                        "rx_drops_velocity", "tx_drops_velocity",
                        "rx_errors_velocity", "tx_errors_velocity"]
        explanations = []
        for idx in top_indices:
            if idx < len(feature_names):
                explanations.append(feature_names[idx])
        return f"Metrics anomaly: {', '.join(explanations)}"
    
    elif node_type == "Protocol_State":
        feature_names = ["ospf_neighbors", "bgp_peers", "mpls_routes"]
        explanations = []
        for idx in top_indices:
            if idx < len(feature_names):
                explanations.append(feature_names[idx])
        return f"Protocol anomaly: {', '.join(explanations)}"
    
    else:
        return f"Anomaly detected in {node_type}"

def load_models():
    global gb, MODELS
    logger.info("Loading GraphBuilder and Models...")
    
    # Download shared scaler file
    scaler_file = os.path.join(BASE_DIR, "shared_scalers.pkl")
    
    if GCS_BUCKET_NAME:
        logger.info("Downloading shared scaler file from GCS...")
        if download_blob(GCS_BUCKET_NAME, f"models/dgat/dgat_scalers.pkl", scaler_file):
            logger.info("Using DGAT scalers as shared scalers")
        elif download_blob(GCS_BUCKET_NAME, f"models/hetgnn/hetgnn_scalers.pkl", scaler_file):
            logger.info("Using HetGNN scalers as shared scalers")
        elif download_blob(GCS_BUCKET_NAME, f"models/stgnn/stgnn_scalers.pkl", scaler_file):
            logger.info("Using STGNN scalers as shared scalers")
    
    gb = GraphBuilder(scaler_file)
    gb.init_config_encoder()
    
    if os.path.exists(scaler_file):
        logger.info(f"Loading scalers from {scaler_file}")
        gb.load_scalers()
    
    # Node and edge types
    node_types = ["PE Router", "P Router", "CE Router", "Router_Config", "Protocol_State", "Interface", "Interface_Metrics"]
    edge_types = [
        ("PE Router", "Owns", "Interface"),
        ("P Router", "Owns", "Interface"),
        ("CE Router", "Owns", "Interface"),
        ("Interface", "Connected", "Interface"),
        ("PE Router", "Has_Config", "Router_Config"),
        ("PE Router", "Has_Protocol", "Protocol_State"),
        ("P Router", "Has_Config", "Router_Config"),
        ("P Router", "Has_Protocol", "Protocol_State"),
        ("CE Router", "Has_Config", "Router_Config"),
        ("CE Router", "Has_Protocol", "Protocol_State"),
        ("Interface", "Has_Metrics", "Interface_Metrics")
    ]
    metadata = (node_types, edge_types)
    
    input_dims = {
        "PE Router": 1, "P Router": 1, "CE Router": 1,
        "Router_Config": 132, "Protocol_State": 3,  # 132 = 128 hash + 4 explicit RT features
        "Interface": 7, "Interface_Metrics": 12
    }
    
    for name, config in MODELS.items():
        if GCS_BUCKET_NAME:
            logger.info(f"Downloading {name} artifacts from GCS...")
            download_blob(GCS_BUCKET_NAME, f"models/{name}/{config['scaler']}", os.path.join(BASE_DIR, config['scaler']))
            download_blob(GCS_BUCKET_NAME, f"models/{name}/{config['file']}", os.path.join(BASE_DIR, config['file']))
            download_blob(GCS_BUCKET_NAME, f"models/{name}/{config['stats']}", os.path.join(BASE_DIR, config['stats']))

        # Initialize Model
        if name == "stgnn":
            instance = config["class"](metadata, HIDDEN_CHANNELS, OUT_CHANNELS, NUM_LAYERS, 'gru', 12)
        elif name == "dgat":
            instance = config["class"](metadata, HIDDEN_CHANNELS, OUT_CHANNELS, NUM_HEADS, NUM_LAYERS)
        elif name == "hetgnn":
            instance = config["class"](metadata, HIDDEN_CHANNELS, OUT_CHANNELS, NUM_LAYERS)
        else:
            instance = config["class"](metadata, HIDDEN_CHANNELS, OUT_CHANNELS, NUM_HEADS, NUM_LAYERS)
            
        instance.set_input_dims(input_dims)
        path = os.path.join(BASE_DIR, config['file'])
        
        if os.path.exists(path):
            try:
                instance.load_state_dict(torch.load(path))
                instance.eval()
                MODELS[name]["instance"] = instance
                logger.info(f"{name.upper()} model loaded successfully.")
            except Exception as e:
                logger.error(f"Failed to load {name.upper()}: {e}")
        
        # Load cluster statistics
        stats_path = os.path.join(BASE_DIR, config['stats'])
        if os.path.exists(stats_path):
            try:
                MODELS[name]["cluster_stats"] = torch.load(stats_path)
                logger.info(f"{name.upper()} cluster statistics loaded successfully.")
                # Log cluster info
                for nt, stats in MODELS[name]["cluster_stats"]['cluster_stats'].items():
                    logger.info(f"  {nt}: {stats['sample_count']} samples, embedding_dim={stats['mean'].shape[0]}")
            except Exception as e:
                logger.error(f"Failed to load {name.upper()} stats: {e}")

async def run_inference():
    global MODELS, gb
    logger.info("="*60)
    logger.info("EXECUTING MULTI-MODEL INFERENCE RUN")
    logger.info("="*60)
        
    if not gb:
        logger.info("GraphBuilder not initialized, loading models...")
        load_models()

    try:
        # Fetch latest Spanner topology
        logger.info(f"Fetching latest snapshot from Spanner")
        dataset = SpannerDataset(SPANNER_INSTANCE, SPANNER_DATABASE, num_snapshots=1, interval_minutes=INTERVAL_MINUTES)
        timestamps = dataset._get_timestamps()
        latest_ts = timestamps[-1]
        logger.info(f"Latest timestamp: {latest_ts}")
        
        data = dataset.fetch_snapshot(latest_ts)
        if not data["nodes"]:
            logger.warning("No data found in Spanner snapshot")
            return {'error': 'No data found in Spanner snapshot'}

        logger.debug(f"Snapshot contains {len(data['nodes'])} nodes, {len(data.get('edges', []))} edges")
        
        hdata, input_dims = gb.process_snapshot(data)
        logger.info("Snapshot processed into HeteroData")
        
        # Run HetGNN inference (primary model for config/protocol/metrics separation)
        logger.info("Running HetGNN inference...")
        hetgnn_model = MODELS["hetgnn"]["instance"]
        hetgnn_stats = MODELS["hetgnn"].get("cluster_stats")
        
        if not hetgnn_stats:
            logger.error("HetGNN cluster statistics not loaded!")
            return {'error': 'Cluster statistics not available'}
        
        with torch.no_grad():
            recon_dict, embeddings = hetgnn_model(hdata.x_dict, hdata.edge_index_dict)
        
        logger.debug(f"HetGNN embeddings: {list(embeddings.keys())}")
        
        # DEBUG: Log RT features for all Router_Config nodes (from INPUT features, not output embeddings)
        if "Router_Config" in hdata.x_dict:
            logger.info("=" * 60)
            logger.info("DEBUG: Router_Config RT Features (dims 128-131 of INPUT)")
            logger.info("=" * 60)
            rev_id_map_config = {v: k for k, v in gb.global_id_map["Router_Config"].items()}
            input_features = hdata.x_dict["Router_Config"]
            for node_idx in range(input_features.size(0)):
                node_id = rev_id_map_config.get(node_idx, f"unknown_{node_idx}")
                # Get RT features from input (last 4 dimensions of 132-dim input)
                if input_features.size(1) >= 132:
                    rt_import_as = input_features[node_idx, 128].item()
                    rt_import_val = input_features[node_idx, 129].item()
                    rt_export_as = input_features[node_idx, 130].item()
                    rt_export_val = input_features[node_idx, 131].item()
                    logger.info(f"  {node_id}:")
                    logger.info(f"    RT_import_AS={rt_import_as:.4f}, RT_import_val={rt_import_val:.4f}")
                    logger.info(f"    RT_export_AS={rt_export_as:.4f}, RT_export_val={rt_export_val:.4f}")
                else:
                    logger.warning(f"  {node_id}: Input dimension is {input_features.size(1)}, expected 132")
            logger.info("=" * 60)
        
        # Compute anomaly scores with adaptive thresholds
        hetgnn_scores, hetgnn_thresholds = compute_anomaly_scores(embeddings, hetgnn_stats, "hetgnn")
        
        logger.info(f"Computed anomaly scores for {len(hetgnn_scores)} node types")
        logger.info(f"Adaptive thresholds per node type:")
        for nt, thresh in hetgnn_thresholds.items():
            logger.info(f"  {nt}: {thresh:.2f}")
        
        # === MULTI-LEVEL ANOMALY DETECTION ===
        consolidated_nodes = {}
        anomaly_count = 0
        
        for node_type, score_dict in hetgnn_scores.items():
            for node_idx, score in score_dict.items():
                # Map internal index back to node ID
                if node_type not in gb.global_id_map:
                    continue
                    
                rev_id_map = {v: k for k, v in gb.global_id_map[node_type].items()}
                if node_idx not in rev_id_map:
                    continue
                
                node_id = rev_id_map[node_idx]
                
                # Initialize node entry
                consolidated_nodes[node_id] = {
                    "id": node_id,
                    "type": node_type,
                    "hetgnn_score": float(score),
                    "hetgnn_embedding": embeddings[node_type][node_idx].tolist(),
                    "anomaly_explanation": None
                }
                
                # Check if anomalous using adaptive threshold for this node type
                node_threshold = hetgnn_thresholds.get(node_type, 5.0)
                if score > node_threshold:
                    anomaly_count += 1
                    logger.info(f"⚠️  Anomaly detected: {node_id} ({node_type}) score={score:.2f} (threshold={node_threshold:.2f})")
                    
                    # Compute feature attribution
                    try:
                        cluster_stats = hetgnn_stats['cluster_stats'][node_type]
                        importance = compute_feature_attribution(
                            hetgnn_model,
                            hdata.x_dict,
                            hdata.edge_index_dict,
                            node_type,
                            node_idx,
                            cluster_stats
                        )
                        
                        explanation = explain_anomaly(importance, node_type, gb)
                        consolidated_nodes[node_id]["anomaly_explanation"] = explanation
                        logger.info(f"   Explanation: {explanation}")
                    except Exception as e:
                        logger.error(f"Failed to compute attribution for {node_id}: {e}")
                        consolidated_nodes[node_id]["anomaly_explanation"] = f"Anomaly detected (score={score:.2f})"
        
        # === PROPAGATE SUB-NODE ANOMALIES TO PARENT NODES ===
        logger.info("Propagating sub-node anomalies to parent nodes...")
        
        for node_id, node_data in list(consolidated_nodes.items()):
            # Router_Config -> Router
            if node_data["type"] == "Router_Config" and node_data["anomaly_explanation"]:
                parent_id = node_id.replace("_config", "")
                parent_type = None
                for rt in ["PE Router", "P Router", "CE Router"]:
                    if parent_id in gb.global_id_map.get(rt, {}):
                        parent_type = rt
                        break
                
                if parent_type:
                    if parent_id not in consolidated_nodes:
                        # Create parent node entry
                        consolidated_nodes[parent_id] = {
                            "id": parent_id,
                            "type": parent_type,
                            "hetgnn_score": 0.0,
                            "hetgnn_embedding": [],
                            "anomaly_explanation": None
                        }
                    
                    # Propagate anomaly
                    consolidated_nodes[parent_id]["anomaly_explanation"] = (
                        f"Config sub-node anomaly: {node_data['anomaly_explanation']}"
                    )
                    logger.info(f"   Propagated to parent: {parent_id} ({parent_type})")
            
            # Interface_Metrics -> Interface
            elif node_data["type"] == "Interface_Metrics" and node_data["anomaly_explanation"]:
                parent_id = node_id.replace("_metrics", "")
                if parent_id in gb.global_id_map.get("Interface", {}):
                    if parent_id not in consolidated_nodes:
                        consolidated_nodes[parent_id] = {
                            "id": parent_id,
                            "type": "Interface",
                            "hetgnn_score": 0.0,
                            "hetgnn_embedding": [],
                            "anomaly_explanation": None
                        }
                    
                    # Add metrics anomaly note (don't overwrite existing explanation)
                    if not consolidated_nodes[parent_id]["anomaly_explanation"]:
                        consolidated_nodes[parent_id]["anomaly_explanation"] = (
                            f"Metrics sub-node anomaly: {node_data['anomaly_explanation']}"
                        )
                        logger.info(f"   Propagated to parent: {parent_id} (Interface)")
        
        logger.info(f"Total anomalies detected: {anomaly_count}")
        
        # Write to Spanner
        mutations = []
        import uuid
        spanner_timestamp = spanner.COMMIT_TIMESTAMP
        
        for node_id, node_data in consolidated_nodes.items():
            embedding_id = str(uuid.uuid4())
            # Map internal GNN type to Spanner schema type
            spanner_node_type = map_to_spanner_node_type(node_data["type"])
            
            # Convert anomaly_explanation to JSON string if present
            anomaly_exp = node_data.get("anomaly_explanation")
            if anomaly_exp is not None:
                # Wrap in JSON object for Spanner JSON column
                anomaly_exp_json = json.dumps({"explanation": anomaly_exp})
            else:
                anomaly_exp_json = None
            
            mutations.append((
                embedding_id,
                node_id,
                spanner_node_type,  # Use mapped type for Spanner
                [],  # stgnn_embedding (not using for now)
                0.0,  # stgnn_score
                [],  # dgat_embedding
                0.0,  # dgat_score
                node_data.get("hetgnn_embedding", []),
                node_data.get("hetgnn_score", 0.0),
                anomaly_exp_json,  # JSON-encoded explanation
                spanner_timestamp
            ))
        
        if mutations:
            logger.info(f"Writing {len(mutations)} embeddings to Spanner...")
            spanner_client = spanner.Client()
            instance = spanner_client.instance(SPANNER_INSTANCE)
            database = instance.database(SPANNER_DATABASE)
            
            try:
                with database.batch() as batch:
                    batch.insert(
                        table="NodeEmbedding",
                        columns=("id", "node_id", "node_type", 
                                 "stgnn_embedding", "stgnn_score", 
                                 "dgat_embedding", "dgat_score", 
                                 "hetgnn_embedding", "hetgnn_score", 
                                 "anomaly_explanation", "timestamp"),
                        values=mutations
                    )
                logger.info("✅ Successfully wrote embeddings to Spanner")
            except Exception as e:
                logger.error(f"Failed to write to Spanner: {e}")
        
        return {"nodes": list(consolidated_nodes.values()), "anomaly_count": anomaly_count}
            
    except Exception as e:
        logger.error(f"Inference run failed: {e}", exc_info=True)
        return {'error': str(e)}

async def predict_handler(request):
    logger.info("Received prediction request")
    results = await run_inference()
    if 'error' in results:
        return web.json_response({"predictions": [], "error": results['error']}, status=500)
    return web.json_response({"predictions": results.get("nodes", []), "anomaly_count": results.get("anomaly_count", 0)})

async def health_handler(request):
    return web.json_response({"status": "healthy"}, status=200)

app = web.Application()
cors = aiohttp_cors.setup(app, defaults={
    "*": aiohttp_cors.ResourceOptions(
        allow_credentials=True,
        expose_headers="*",
        allow_headers="*"
    )
})

if __name__ == "__main__":
    load_models()
    
    predict_route_path = os.environ.get('AIP_PREDICT_ROUTE', '/predict')
    health_route_path = os.environ.get('AIP_HEALTH_ROUTE', '/health')
    
    predict_route = app.router.add_post(predict_route_path, predict_handler)
    health_route = app.router.add_get(health_route_path, health_handler)
    
    cors.add(predict_route)
    cors.add(health_route)

    logger.info("Serving GNN on Vertex AI...")
    port = int(os.environ.get('AIP_HTTP_PORT', 8080))
    web.run_app(app, port=port)
