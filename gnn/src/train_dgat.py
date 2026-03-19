import os
import sys
import pickle
import torch
import torch.nn as nn
from tqdm import tqdm
from google.cloud import storage
import joblib
import logging
from model.dgat import DGAT
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
MODEL_SAVE_PATH = "dgat_model.pth"
SCALER_PATH = "dgat_scalers.pkl"
EPOCHS = 20  # Reduced from 50 - DGAT with attention is expensive
LEARNING_RATE = 0.001
TRAINING_SNAPSHOTS = 30  # Reduced from 60 - still enough for healthy baseline
VALIDATION_SPLIT = 0.2  # 20% for validation
EARLY_STOPPING_PATIENCE = 5  # Reduced from 10 - stop earlier if not improving
MIN_DELTA = 0.001  # Minimum change to qualify as an improvement

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

def run_training_pipeline():
    logger.info("="*60)
    logger.info(f"DGAT TRAINING SERVICE STARTED")
    logger.info(f"Instance: {SPANNER_INSTANCE}, Database: {SPANNER_DATABASE}")
    logger.info("="*60)

    try:
        gb = GraphBuilder(SCALER_PATH)
        gb.init_config_encoder()

        # ── Data loading: GCS path (Vertex AI pipeline) or live Spanner (local/legacy) ──
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

        logger.info("Fitting scalers...")
        gb.fit_scalers(snapshot_objects)
        gb.save_scalers()

        logger.info("Processing snapshots into HeteroData...")
        snapshots = []
        input_dims = None
        
        for idx, data in enumerate(snapshot_objects):
            logger.debug(f"Processing snapshot {idx+1}/{len(snapshot_objects)}")
            # Assume gb.process_snapshot() natively extracts edge directionality metrics
            kwargs = {}
            if hasattr(gb, 'include_asymmetry_features'):
                kwargs['include_asymmetry_features'] = True
                
            hdata, dims = gb.process_snapshot(data, **kwargs)
            snapshots.append(hdata)
            if input_dims is None:
                input_dims = dims
                logger.info(f"Input dimensions: {input_dims}")

        node_types = list(input_dims.keys())
        all_edge_types = set()
        for s in snapshots:
            for et in s.edge_index_dict.keys():
                all_edge_types.add(et)
        
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
        
        # Split data into train and validation sets
        split_idx = int(len(snapshots) * (1 - VALIDATION_SPLIT))
        train_snapshots = snapshots[:split_idx]
        val_snapshots = snapshots[split_idx:]
        
        logger.info(f"Dataset split: {len(train_snapshots)} training, {len(val_snapshots)} validation snapshots")
        
        logger.info(f"Creating DGAT model with hidden_channels={HIDDEN_CHANNELS}, num_heads={NUM_HEADS}, num_layers={NUM_LAYERS}")
        model = DGAT(metadata, HIDDEN_CHANNELS, OUT_CHANNELS, NUM_HEADS, NUM_LAYERS)
        model.set_input_dims(input_dims)
        
        optimizer = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE)
        # Add learning rate scheduler to reduce LR when validation loss plateaus
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimizer, mode='min', factor=0.5, patience=5, min_lr=1e-6
        )
        criterion = nn.MSELoss(reduction='sum')
        
        logger.info("Model created (parameters will be initialized on first forward pass)")
        logger.info(f"Learning rate scheduler: ReduceLROnPlateau (factor=0.5, patience=5)")
        
        logger.info(f"Starting training for up to {EPOCHS} epochs with early stopping (patience={EARLY_STOPPING_PATIENCE})")
        logger.info(f"Multi-head attention with {NUM_HEADS} heads for directional anomaly detection")
        
        # Early stopping variables
        best_val_loss = float('inf')
        epochs_without_improvement = 0
        best_model_state = None
        
        for epoch in range(EPOCHS):
            # ============ TRAINING PHASE ============
            model.train()
            train_losses = []
            train_losses_by_type = {nt: [] for nt in node_types}
            
            for snap_idx, snapshot in enumerate(train_snapshots):
                optimizer.zero_grad()
                
                # Log first forward pass details
                if epoch == 0 and snap_idx == 0:
                    logger.debug(f"First forward pass - input node types: {list(snapshot.x_dict.keys())}")
                    logger.debug(f"First forward pass - edge types: {list(snapshot.edge_index_dict.keys())}")
                    for nt, x in snapshot.x_dict.items():
                        logger.debug(f"  {nt} input shape: {x.shape}")
                
                # Pass edge attributes if available for asymmetry-aware attention
                edge_attr_dict = snapshot.edge_attr_dict if hasattr(snapshot, 'edge_attr_dict') else None
                recon_dict, embeddings = model(snapshot.x_dict, snapshot.edge_index_dict, edge_attr_dict)
                
                # Log reconstruction output
                if epoch == 0 and snap_idx == 0:
                    logger.debug(f"Reconstruction output node types: {list(recon_dict.keys())}")
                    for nt, recon_x in recon_dict.items():
                        logger.debug(f"  {nt} reconstruction shape: {recon_x.shape}")
                    logger.debug(f"Embedding output node types: {list(embeddings.keys())}")
                    for nt, emb in embeddings.items():
                        logger.debug(f"  {nt} embedding shape: {emb.shape}")
                    if edge_attr_dict:
                        logger.debug(f"Edge attributes provided for {len(edge_attr_dict)} edge types")
                
                loss = 0
                for node_type, recon_x in recon_dict.items():
                    if node_type in snapshot.x_dict:
                        # Standard reconstruction loss
                        node_loss = criterion(recon_x, snapshot.x_dict[node_type])
                        loss += node_loss
                        train_losses_by_type[node_type].append(node_loss.detach())
                        if epoch == 0 and snap_idx == 0:
                            logger.debug(f"  {node_type} loss: {node_loss.item():.4f}")
                
                loss.backward()
                optimizer.step()
                
                # Accumulate loss as tensor (detached from computation graph)
                train_losses.append(loss.detach())
            
            # Calculate training loss
            train_loss = torch.stack(train_losses).sum().item()
            
            # ============ VALIDATION PHASE ============
            model.eval()
            val_losses = []
            val_losses_by_type = {nt: [] for nt in node_types}
            
            with torch.no_grad():
                for snapshot in val_snapshots:
                    edge_attr_dict = snapshot.edge_attr_dict if hasattr(snapshot, 'edge_attr_dict') else None
                    recon_dict, embeddings = model(snapshot.x_dict, snapshot.edge_index_dict, edge_attr_dict)
                    
                    loss = 0
                    for node_type, recon_x in recon_dict.items():
                        if node_type in snapshot.x_dict:
                            node_loss = criterion(recon_x, snapshot.x_dict[node_type])
                            loss += node_loss
                            val_losses_by_type[node_type].append(node_loss.detach())
                    
                    val_losses.append(loss.detach())
            
            val_loss = torch.stack(val_losses).sum().item()
            
            # Step the learning rate scheduler
            scheduler.step(val_loss)
            current_lr = optimizer.param_groups[0]['lr']
            
            # ============ LOGGING ============
            if (epoch + 1) % 5 == 0:
                logger.info(f"Epoch {epoch+1}/{EPOCHS}, Train Loss: {train_loss:.4f}, Val Loss: {val_loss:.4f}, LR: {current_lr:.6f}")
                # Log per-node-type losses
                logger.info("  Per-node-type validation losses:")
                for nt in node_types:
                    if val_losses_by_type[nt]:
                        nt_loss = torch.stack(val_losses_by_type[nt]).sum().item()
                        logger.info(f"    {nt}: {nt_loss:.4f}")
            else:
                logger.debug(f"Epoch {epoch+1}/{EPOCHS}, Train Loss: {train_loss:.4f}, Val Loss: {val_loss:.4f}")
            
            # ============ EARLY STOPPING CHECK ============
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
                    # Restore best model
                    if best_model_state is not None:
                        model.load_state_dict(best_model_state)
                        logger.info("Restored best model weights")
                    break
            
        logger.info(f"Saving model locally to {MODEL_SAVE_PATH}...")
        torch.save(model.state_dict(), MODEL_SAVE_PATH)
        
        # === NEW: Compute cluster statistics for anomaly detection ===
        logger.info("Computing healthy embedding cluster statistics...")
        from collections import defaultdict
        
        node_embeddings = defaultdict(lambda: defaultdict(list))
        
        with torch.no_grad():
            model.eval()
            for snapshot in train_snapshots:
                edge_attr_dict = snapshot.edge_attr_dict if hasattr(snapshot, 'edge_attr_dict') else None
                recon_dict, embeddings = model(snapshot.x_dict, snapshot.edge_index_dict, edge_attr_dict)
                
                for node_type, emb_tensor in embeddings.items():
                    for node_idx in range(emb_tensor.size(0)):
                        node_embeddings[node_type][node_idx].append(
                            emb_tensor[node_idx].cpu()
                        )
        
        # Compute per-node-type global statistics
        cluster_stats = {}
        for node_type, node_dict in node_embeddings.items():
            all_embeddings = []
            for node_idx, emb_list in node_dict.items():
                all_embeddings.extend(emb_list)
            
            if all_embeddings:
                stacked = torch.stack(all_embeddings)
                cluster_stats[node_type] = {
                    'mean': stacked.mean(dim=0),
                    'std': stacked.std(dim=0),
                    'cov': torch.cov(stacked.T) if stacked.size(0) > 1 else torch.eye(stacked.size(1)),
                    'sample_count': len(all_embeddings)
                }
                logger.info(f"  {node_type}: {len(all_embeddings)} healthy samples, embedding_dim={stacked.size(1)}")
        
        # Compute per-node statistics (for nodes appearing consistently)
        node_stats = {}
        for node_type, node_dict in node_embeddings.items():
            node_stats[node_type] = {}
            for node_idx, emb_list in node_dict.items():
                if len(emb_list) >= 5:
                    stacked = torch.stack(emb_list)
                    node_stats[node_type][node_idx] = {
                        'mean': stacked.mean(dim=0),
                        'std': stacked.std(dim=0)
                    }
        
        # Save statistics
        stats_data = {
            'cluster_stats': cluster_stats,
            'node_stats': node_stats,
            'gb_id_map': gb.global_id_map
        }
        
        STATS_SAVE_PATH = MODEL_SAVE_PATH.replace('.pth', '_stats.pth')
        torch.save(stats_data, STATS_SAVE_PATH)
        logger.info(f"Saved cluster statistics to {STATS_SAVE_PATH}")
        
        if GCS_BUCKET_NAME:
            upload_blob(GCS_BUCKET_NAME, MODEL_SAVE_PATH, f"models/dgat/{MODEL_SAVE_PATH}")
            upload_blob(GCS_BUCKET_NAME, SCALER_PATH, f"models/dgat/{SCALER_PATH}")
            upload_blob(GCS_BUCKET_NAME, STATS_SAVE_PATH, f"models/dgat/{STATS_SAVE_PATH}")

        # ── Vertex AI: copy artefacts to AIP_MODEL_DIR (canonical output for KFP component) ──
        if AIP_MODEL_DIR:
            logger.info(f"Copying artefacts to Vertex AI model dir: {AIP_MODEL_DIR}")
            bucket_name = AIP_MODEL_DIR.replace("gs://", "").split("/")[0]
            prefix = "/".join(AIP_MODEL_DIR.replace("gs://", "").split("/")[1:]).rstrip("/")
            for local_path, dest_name in [
                (MODEL_SAVE_PATH, "model.pth"),
                (SCALER_PATH, "scalers.pkl"),
                (STATS_SAVE_PATH, "model_stats.pth"),
            ]:
                upload_blob(bucket_name, local_path, f"{prefix}/{dest_name}")
            logger.info(f"Artefacts copied to {AIP_MODEL_DIR}")
            
    except Exception as e:
        logger.error(f"Training pipeline failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    run_training_pipeline()
