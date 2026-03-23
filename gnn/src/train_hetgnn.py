import os
import sys
import pickle
import torch
import torch.nn as nn
from tqdm import tqdm
from google.cloud import storage
import joblib
import logging
from model.hetgnn import HetGNN
from utils.data import SpannerDataset
from utils.gnn_utils import SPANNER_INSTANCE, SPANNER_DATABASE, GCS_BUCKET_NAME, INTERVAL_MINUTES, GraphBuilder, HIDDEN_CHANNELS, OUT_CHANNELS, NUM_HEADS, NUM_LAYERS

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Vertex AI env vars — set by the KFP training component
SNAPSHOTS_GCS_PATH = os.getenv("SNAPSHOTS_GCS_PATH", "")
SCALERS_GCS_PATH = os.getenv("SCALERS_GCS_PATH", "")
AIP_MODEL_DIR = os.getenv("AIP_MODEL_DIR", "")  # Vertex AI canonical output dir


def load_snapshots_from_gcs(gcs_path: str) -> list:
    """Download serialised snapshot dicts from GCS (set by ingest_snapshots KFP component)."""
    storage_client = storage.Client()
    bucket_name = gcs_path.replace("gs://", "").split("/")[0]
    prefix = "/".join(gcs_path.replace("gs://", "").split("/")[1:])
    bucket = storage_client.bucket(bucket_name)
    blobs = sorted(
        [b for b in bucket.list_blobs(prefix=prefix) if b.name.endswith(".pkl")],
        key=lambda b: b.name,
    )
    snapshots = []
    for blob in blobs:
        snapshots.append(pickle.loads(blob.download_as_bytes()))
    logger.info(f"Loaded {len(snapshots)} snapshots from {gcs_path}")
    return snapshots


# Configuration
EPOCHS = 50
LEARNING_RATE = 0.001
TRAINING_SNAPSHOTS = 100
VALIDATION_SPLIT = 0.2  # 20% for validation
EARLY_STOPPING_PATIENCE = 10  # Stop if no improvement for 10 epochs
MIN_DELTA = 0.001  # Minimum change to qualify as an improvement

# Multi-task objective weights (bgp_session removed — redistributed to router)
ALPHA = 0.6  # Weight for Router Loss
GAMMA = 0.4  # Weight for Interface Loss
DIVERSITY_WEIGHT = 0.1  # Weight for contrastive diversity penalty (10%)

def contrastive_diversity_loss(embeddings_dict):
    """
    Penalizes embeddings that are too similar within a node type.
    Encourages diversity in the latent space to prevent representation collapse.
    
    Args:
        embeddings_dict: Dictionary of {node_type: embedding_tensor}
    
    Returns:
        Scalar diversity loss (higher = more similar = worse)
    """
    diversity_loss = 0.0
    count = 0
    
    for node_type, emb_tensor in embeddings_dict.items():
        if emb_tensor.size(0) > 1:  # Need at least 2 nodes to compute similarity
            # Normalize embeddings to unit length for cosine similarity
            normalized = torch.nn.functional.normalize(emb_tensor, p=2, dim=1)
            
            # Compute pairwise cosine similarity matrix
            similarity_matrix = normalized @ normalized.T
            
            # Create mask to exclude diagonal (self-similarity = 1.0)
            mask = 1 - torch.eye(emb_tensor.size(0), device=emb_tensor.device)
            
            # Sum off-diagonal similarities
            # High similarity between different nodes = bad (we want diversity)
            off_diagonal_sim = (similarity_matrix * mask).sum()
            
            # Average over all pairs
            num_pairs = emb_tensor.size(0) * (emb_tensor.size(0) - 1)
            diversity_loss += off_diagonal_sim / num_pairs
            count += 1
    
    # Average across all node types that had multiple nodes
    return diversity_loss / count if count > 0 else torch.tensor(0.0)

def upload_blob(bucket_name, source_file_name, destination_blob_name):
    """Uploads a file to the bucket."""
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(destination_blob_name)
        blob.upload_from_filename(source_file_name)
        logger.info(f"File {source_file_name} uploaded to {destination_blob_name}.")
    except Exception as e:
        logger.error(f"Failed to upload {source_file_name} to GCS: {e}")


def train_hetgnn_on_snapshots(
    snapshot_objects: list,
    output_dir: str = ".",
    hidden_channels: int = HIDDEN_CHANNELS,
    num_layers: int = NUM_LAYERS,
    epochs_override: int = None,
) -> tuple:
    """Core HetGNN training function. Accepts pre-loaded snapshot dicts.

    Fits scalers, converts snapshots to HeteroData, trains with early stopping,
    computes cluster statistics for anomaly detection, and saves all artefacts
    to ``output_dir``.

    Artefacts written:
      ``output_dir/model.pth``        — model checkpoint (state_dict + metadata)
      ``output_dir/scalers.pkl``      — fitted GraphBuilder scalers + id_map
      ``output_dir/model_stats.pth``  — healthy-embedding cluster statistics

    Args:
        snapshot_objects: List of snapshot dicts from Spanner or local pkl files.
        output_dir:       Directory to write artefacts (created if absent).
        hidden_channels:  Hidden dimension for HetGNN layers.
        num_layers:       Number of HetGNN message-passing layers.
        epochs_override:  Override the EPOCHS constant (useful for quick local runs).

    Returns:
        (model, best_val_loss, graph_builder)
    """
    from collections import defaultdict
    from pathlib import Path as _Path

    out = _Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    local_scaler_path = str(out / "scalers.pkl")
    local_model_path  = str(out / "model.pth")
    local_stats_path  = str(out / "model_stats.pth")

    # ── Fit scalers ──────────────────────────────────────────────────────────
    gb = GraphBuilder(local_scaler_path)
    gb.init_config_encoder()
    logger.info("Fitting scalers...")
    gb.fit_scalers(snapshot_objects)
    gb.save_scalers()  # saves to local_scaler_path

    # ── Convert to HeteroData ────────────────────────────────────────────────
    logger.info("Processing snapshots into HeteroData...")
    snapshots = []
    input_dims = None
    for idx, data in enumerate(snapshot_objects):
        logger.debug(f"Processing snapshot {idx+1}/{len(snapshot_objects)}")
        hdata, dims = gb.process_snapshot(data)
        snapshots.append(hdata)
        if input_dims is None:
            input_dims = dims
            logger.info(f"Input dimensions: {input_dims}")

    node_types = list(input_dims.keys())
    all_edge_types = set()
    for s in snapshots:
        all_edge_types.update(s.edge_index_dict.keys())
    metadata = (node_types, list(all_edge_types))

    logger.info(f"Node types ({len(node_types)}): {node_types}")
    logger.info(f"Edge types ({len(all_edge_types)}): {list(all_edge_types)}")

    # Log snapshot statistics
    if snapshots:
        sample = snapshots[0]
        logger.info("Sample snapshot statistics:")
        for nt in node_types:
            if nt in sample.x_dict:
                logger.info(f"  {nt}: {sample.x_dict[nt].shape[0]} nodes, {sample.x_dict[nt].shape[1]} features")
        for et in all_edge_types:
            if et in sample.edge_index_dict:
                logger.info(f"  {et}: {sample.edge_index_dict[et].shape[1]} edges")

    # ── Train / val split ────────────────────────────────────────────────────
    split_idx = int(len(snapshots) * (1 - VALIDATION_SPLIT))
    train_snapshots = snapshots[:split_idx]
    val_snapshots   = snapshots[split_idx:]
    logger.info(f"Dataset split: {len(train_snapshots)} training, {len(val_snapshots)} validation snapshots")

    # ── Build model ──────────────────────────────────────────────────────────
    out_channels = hidden_channels  # autoencoder symmetric dims
    logger.info(f"Creating HetGNN model with hidden_channels={hidden_channels}, num_layers={num_layers}")
    model = HetGNN(metadata, hidden_channels, out_channels, num_layers)
    model.set_input_dims(input_dims)

    optimizer = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode='min', factor=0.5, patience=5, min_lr=1e-6
    )
    criterion = nn.MSELoss(reduction='sum')

    logger.info("Model created (parameters will be initialized on first forward pass)")
    logger.info(f"Learning rate scheduler: ReduceLROnPlateau (factor=0.5, patience=5)")

    max_epochs = epochs_override if epochs_override is not None else EPOCHS
    logger.info(f"Starting training for up to {max_epochs} epochs with early stopping (patience={EARLY_STOPPING_PATIENCE})")
    logger.info(f"Multi-task weights: α={ALPHA} (router), γ={GAMMA} (interface), diversity={DIVERSITY_WEIGHT}")

    # ── Training loop ────────────────────────────────────────────────────────
    best_val_loss = float('inf')
    epochs_without_improvement = 0
    best_model_state = None

    for epoch in range(max_epochs):
        # ── Training phase ────────────────────────────────────────────────────
        model.train()
        train_loss_tensors = []
        train_router_tensors = []
        train_interface_tensors = []
        train_diversity_tensors = []

        for snap_idx, snapshot in enumerate(train_snapshots):
            optimizer.zero_grad()

            if epoch == 0 and snap_idx == 0:
                logger.debug(f"First forward pass - input node types: {list(snapshot.x_dict.keys())}")
                logger.debug(f"First forward pass - edge types: {list(snapshot.edge_index_dict.keys())}")

            recon_dict, embeddings = model(snapshot.x_dict, snapshot.edge_index_dict)

            if epoch == 0 and snap_idx == 0:
                logger.debug(f"Reconstruction output node types: {list(recon_dict.keys())}")
                logger.debug(f"Embedding output node types: {list(embeddings.keys())}")

            loss_router = 0
            loss_interface = 0
            for node_type, recon_x in recon_dict.items():
                if node_type in snapshot.x_dict:
                    node_loss = criterion(recon_x, snapshot.x_dict[node_type])
                    if node_type == "router":
                        loss_router += node_loss
                    elif node_type == "interface":
                        loss_interface += node_loss

            diversity_loss = contrastive_diversity_loss(embeddings)
            total_loss = (ALPHA * loss_router) + (GAMMA * loss_interface) + (DIVERSITY_WEIGHT * diversity_loss)
            total_loss.backward()
            optimizer.step()

            train_loss_tensors.append(total_loss.detach())
            if isinstance(loss_router, torch.Tensor) and loss_router.numel() > 0:
                train_router_tensors.append(loss_router.detach())
            if isinstance(loss_interface, torch.Tensor) and loss_interface.numel() > 0:
                train_interface_tensors.append(loss_interface.detach())
            train_diversity_tensors.append(diversity_loss.detach())

        train_loss = torch.stack(train_loss_tensors).sum().item()
        train_loss_router = torch.stack(train_router_tensors).sum().item() if train_router_tensors else 0.0
        train_loss_interface = torch.stack(train_interface_tensors).sum().item() if train_interface_tensors else 0.0
        train_loss_diversity = torch.stack(train_diversity_tensors).mean().item() if train_diversity_tensors else 0.0

        # ── Validation phase ──────────────────────────────────────────────────
        model.eval()
        val_loss_tensors = []
        val_router_tensors = []
        val_interface_tensors = []
        val_diversity_tensors = []

        with torch.no_grad():
            for snapshot in val_snapshots:
                recon_dict, embeddings = model(snapshot.x_dict, snapshot.edge_index_dict)
                loss_router = 0
                loss_interface = 0
                for node_type, recon_x in recon_dict.items():
                    if node_type in snapshot.x_dict:
                        node_loss = criterion(recon_x, snapshot.x_dict[node_type])
                        if node_type == "router":
                            loss_router += node_loss
                        elif node_type == "interface":
                            loss_interface += node_loss
                diversity_loss = contrastive_diversity_loss(embeddings)
                total_loss = (ALPHA * loss_router) + (GAMMA * loss_interface) + (DIVERSITY_WEIGHT * diversity_loss)
                val_loss_tensors.append(total_loss.detach())
                if isinstance(loss_router, torch.Tensor) and loss_router.numel() > 0:
                    val_router_tensors.append(loss_router.detach())
                if isinstance(loss_interface, torch.Tensor) and loss_interface.numel() > 0:
                    val_interface_tensors.append(loss_interface.detach())
                val_diversity_tensors.append(diversity_loss.detach())

        val_loss = torch.stack(val_loss_tensors).sum().item()
        val_loss_router = torch.stack(val_router_tensors).sum().item() if val_router_tensors else 0.0
        val_loss_interface = torch.stack(val_interface_tensors).sum().item() if val_interface_tensors else 0.0
        val_loss_diversity = torch.stack(val_diversity_tensors).mean().item() if val_diversity_tensors else 0.0

        scheduler.step(val_loss)
        current_lr = optimizer.param_groups[0]['lr']

        if (epoch + 1) % 5 == 0:
            logger.info(f"Epoch {epoch+1}/{max_epochs}, Train Loss: {train_loss:.4f}, Val Loss: {val_loss:.4f}, LR: {current_lr:.6f}")
            logger.info(f"  Train - Router: {train_loss_router:.4f}, Interface: {train_loss_interface:.4f}, Diversity: {train_loss_diversity:.4f}")
            logger.info(f"  Val   - Router: {val_loss_router:.4f}, Interface: {val_loss_interface:.4f}, Diversity: {val_loss_diversity:.4f}")
        else:
            logger.debug(f"Epoch {epoch+1}/{max_epochs}, Train Loss: {train_loss:.4f}, Val Loss: {val_loss:.4f}")

        # ── Early stopping ────────────────────────────────────────────────────
        if val_loss < best_val_loss - MIN_DELTA:
            best_val_loss = val_loss
            epochs_without_improvement = 0
            best_model_state = model.state_dict().copy()
            logger.info(f"  ✓ New best validation loss: {best_val_loss:.4f}")
        else:
            epochs_without_improvement += 1
            if epochs_without_improvement >= EARLY_STOPPING_PATIENCE:
                logger.info(f"Early stopping triggered after {epoch+1} epochs (no improvement for {EARLY_STOPPING_PATIENCE} epochs)")
                logger.info(f"Best validation loss: {best_val_loss:.4f}")
                if best_model_state is not None:
                    model.load_state_dict(best_model_state)
                    logger.info("Restored best model weights")
                break

    # ── Save model checkpoint ────────────────────────────────────────────────
    logger.info(f"Saving model to {local_model_path}...")
    torch.save(
        {
            "model_state_dict": model.state_dict(),
            "hidden_channels":  hidden_channels,
            "num_layers":       num_layers,
            "val_loss":         best_val_loss,
        },
        local_model_path,
    )

    # ── Compute cluster statistics for anomaly detection ─────────────────────
    logger.info("Computing healthy embedding cluster statistics...")
    node_embeddings = defaultdict(lambda: defaultdict(list))
    model.eval()
    with torch.no_grad():
        for snapshot in train_snapshots:
            _, embeddings = model(snapshot.x_dict, snapshot.edge_index_dict)
            for node_type, emb_tensor in embeddings.items():
                for node_idx in range(emb_tensor.size(0)):
                    node_embeddings[node_type][node_idx].append(emb_tensor[node_idx].cpu())

    cluster_stats = {}
    for node_type, node_dict in node_embeddings.items():
        all_embeddings = [e for embs in node_dict.values() for e in embs]
        if all_embeddings:
            stacked = torch.stack(all_embeddings)
            cluster_stats[node_type] = {
                'mean':         stacked.mean(dim=0),
                'std':          stacked.std(dim=0),
                'cov':          torch.cov(stacked.T) if stacked.size(0) > 1 else torch.eye(stacked.size(1)),
                'sample_count': len(all_embeddings),
            }
            logger.info(f"  {node_type}: {len(all_embeddings)} healthy samples, embedding_dim={stacked.size(1)}")

    node_stats = {}
    for node_type, node_dict in node_embeddings.items():
        node_stats[node_type] = {}
        for node_idx, emb_list in node_dict.items():
            if len(emb_list) >= 5:
                stacked = torch.stack(emb_list)
                node_stats[node_type][node_idx] = {
                    'mean': stacked.mean(dim=0),
                    'std':  stacked.std(dim=0),
                }

    stats_data = {
        'cluster_stats': cluster_stats,
        'node_stats':    node_stats,
        'gb_id_map':     gb.global_id_map,
    }
    torch.save(stats_data, local_stats_path)
    logger.info(f"Saved cluster statistics to {local_stats_path}")

    return model, best_val_loss, gb


def run_training_pipeline():
    logger.info("="*60)
    logger.info(f"HETGNN TRAINING SERVICE STARTED")
    logger.info(f"Instance: {SPANNER_INSTANCE}, Database: {SPANNER_DATABASE}")
    logger.info("="*60)

    try:
        # ── Load snapshots: GCS (Vertex AI pipeline) or live Spanner ─────────
        if SNAPSHOTS_GCS_PATH:
            logger.info(f"Loading pre-fetched snapshots from GCS: {SNAPSHOTS_GCS_PATH}")
            snapshot_objects = load_snapshots_from_gcs(SNAPSHOTS_GCS_PATH)
        else:
            logger.info(f"Fetching snapshots live from Spanner: {SPANNER_INSTANCE}/{SPANNER_DATABASE}")
            dataset = SpannerDataset(SPANNER_INSTANCE, SPANNER_DATABASE, num_snapshots=TRAINING_SNAPSHOTS, interval_minutes=INTERVAL_MINUTES)
            timestamps = dataset._get_timestamps()
            snapshot_objects = []
            for ts in tqdm(timestamps, desc="Fetching Snapshots"):
                try:
                    snapshot = dataset.fetch_snapshot(ts)
                    if snapshot["nodes"]:
                        snapshot_objects.append(snapshot)
                except Exception as e:
                    logger.error(f"Error fetching snapshot at {ts}: {e}")

        if not snapshot_objects:
            logger.error("Error: No data found. Exiting.")
            return

        # ── Train (fits scalers, trains, saves artefacts to cwd) ─────────────
        model, best_val_loss, gb = train_hetgnn_on_snapshots(snapshot_objects, output_dir=".")

        # ── Upload to GCS (legacy / standalone path) ──────────────────────────
        if GCS_BUCKET_NAME:
            upload_blob(GCS_BUCKET_NAME, "model.pth",       f"models/hetgnn/model.pth")
            upload_blob(GCS_BUCKET_NAME, "scalers.pkl",     f"models/hetgnn/scalers.pkl")
            upload_blob(GCS_BUCKET_NAME, "model_stats.pth", f"models/hetgnn/model_stats.pth")

        # ── Copy artefacts to AIP_MODEL_DIR (Vertex AI canonical output) ─────
        if AIP_MODEL_DIR:
            logger.info(f"Copying artefacts to Vertex AI model dir: {AIP_MODEL_DIR}")
            bucket_name = AIP_MODEL_DIR.replace("gs://", "").split("/")[0]
            prefix = "/".join(AIP_MODEL_DIR.replace("gs://", "").split("/")[1:]).rstrip("/")
            for local_name, dest_name in [
                ("model.pth",       "model.pth"),
                ("scalers.pkl",     "scalers.pkl"),
                ("model_stats.pth", "model_stats.pth"),
            ]:
                upload_blob(bucket_name, local_name, f"{prefix}/{dest_name}")
            logger.info(f"Artefacts copied to {AIP_MODEL_DIR}")

    except Exception as e:
        logger.error(f"Training pipeline failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    run_training_pipeline()
