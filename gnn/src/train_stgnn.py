import os
import sys
import pickle
import torch
import torch.nn as nn
from tqdm import tqdm
from google.cloud import storage
import joblib
import logging
from model.stgnn import STGNN
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
MODEL_SAVE_PATH = "stgnn_model.pth"
SCALER_PATH = "stgnn_scalers.pkl"
EPOCHS = 30
LEARNING_RATE = 0.001
TRAINING_SNAPSHOTS = 24  # 24 snapshots = 24 minutes of temporal data history (at 1-min intervals)
TEMPORAL_STEPS = 12 # 12-minute sequence window (12 timesteps at 1-min intervals)
VALIDATION_SPLIT = 0.2  # 20% for validation
EARLY_STOPPING_PATIENCE = 10  # Stop if no improvement for 10 epochs
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
    logger.info(f"STGNN TRAINING SERVICE STARTED")
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

        logger.info("Processing snapshots into HeteroData batches...")
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
        
        # Build multi-resolution temporal sequences for next-step prediction
        # STGNN expects x_dict_seq where values are [N, T, F] tensors
        # For next-step prediction: use snapshots [i:i+T] to predict snapshot [i+T]
        logger.info(f"Building temporal sequences with window size T={TEMPORAL_STEPS} for next-step prediction")
        temporal_blocks = []
        for i in range(len(snapshots) - TEMPORAL_STEPS - 1):  # Need T+1 snapshots for prediction
            # Input: snapshots [i:i+TEMPORAL_STEPS]
            # Target: snapshot [i+TEMPORAL_STEPS]
            input_snapshots = snapshots[i : i + TEMPORAL_STEPS]
            target_snapshot = snapshots[i + TEMPORAL_STEPS]
            
            x_dict_seq = {}
            target_x_dict = {}
            for nt in node_types:
                # Get the node features for this node_type across T snapshots [T, N, F]
                n_t_features = []
                for s in input_snapshots:
                    n_t_features.append(s.x_dict.get(nt, torch.zeros((1, input_dims[nt]))))
                    
                # Stack to [N, T, F] - input sequence
                stacked = torch.stack(n_t_features, dim=1)
                x_dict_seq[nt] = stacked
                
                # Target is the NEXT time step (T+1)
                target_x_dict[nt] = target_snapshot.x_dict.get(nt, torch.zeros((1, input_dims[nt])))
            
            # Assume topology static over short temporal window, use target snapshot's topology
            temporal_blocks.append({
                "x_dict_seq": x_dict_seq,
                "target_x_dict": target_x_dict,
                "edge_index_dict": target_snapshot.edge_index_dict
            })
        
        logger.info(f"Created {len(temporal_blocks)} temporal sequence blocks (T={TEMPORAL_STEPS})")
        
        # Log temporal block statistics
        if temporal_blocks:
            sample_block = temporal_blocks[0]
            logger.info("Sample temporal block statistics:")
            for nt, seq in sample_block["x_dict_seq"].items():
                logger.info(f"  {nt}: shape {seq.shape} [nodes, time_steps, features]")
        
        # Split temporal blocks into train and validation sets
        split_idx = int(len(temporal_blocks) * (1 - VALIDATION_SPLIT))
        train_blocks = temporal_blocks[:split_idx]
        val_blocks = temporal_blocks[split_idx:]
        
        logger.info(f"Temporal blocks split: {len(train_blocks)} training, {len(val_blocks)} validation")
        
        logger.info(f"Creating STGNN model with hidden_channels={HIDDEN_CHANNELS}, num_layers={NUM_LAYERS}, rnn_type=GRU")
        model = STGNN(metadata, HIDDEN_CHANNELS, OUT_CHANNELS, NUM_LAYERS, 'gru', TEMPORAL_STEPS)
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
        logger.info(f"Temporal sequence length: {TEMPORAL_STEPS} steps, using stateful RNN across blocks")
        
        # Early stopping variables
        best_val_loss = float('inf')
        epochs_without_improvement = 0
        best_model_state = None
        
        for epoch in range(EPOCHS):
            # ============ TRAINING PHASE ============
            model.train()
            train_losses = []
            train_losses_by_type = {nt: [] for nt in node_types}
            # RNN hidden states can persist across batches for long-term dynamics 
            hidden_state = None 
            
            for block_idx, block in enumerate(train_blocks):
                optimizer.zero_grad()
                
                # Log first forward pass details
                if epoch == 0 and block_idx == 0:
                    logger.debug(f"First forward pass - input node types: {list(block['x_dict_seq'].keys())}")
                    logger.debug(f"First forward pass - edge types: {list(block['edge_index_dict'].keys())}")
                    for nt, seq in block["x_dict_seq"].items():
                        logger.debug(f"  {nt} sequence shape: {seq.shape}")
                
                if hidden_state:
                    hidden_state = {k: v.detach() for k, v in hidden_state.items()}
                    
                recon_dict, embeddings, hidden_state = model(block["x_dict_seq"], block["edge_index_dict"], hidden_state)
                
                # Log reconstruction output
                if epoch == 0 and block_idx == 0:
                    logger.debug(f"Reconstruction output node types: {list(recon_dict.keys())}")
                    for nt, recon_x in recon_dict.items():
                        logger.debug(f"  {nt} reconstruction shape: {recon_x.shape}")
                    logger.debug(f"Embedding output node types: {list(embeddings.keys())}")
                    for nt, emb in embeddings.items():
                        logger.debug(f"  {nt} embedding shape: {emb.shape}")
                
                loss = 0
                for node_type, recon_x in recon_dict.items():
                    # Next-step prediction: compare against T+1 (the NEXT snapshot after the sequence)
                    if node_type in block["target_x_dict"]:
                        target_x = block["target_x_dict"][node_type]
                        node_loss = criterion(recon_x, target_x)
                        loss += node_loss
                        train_losses_by_type[node_type].append(node_loss.detach())
                        if epoch == 0 and block_idx == 0:
                            logger.debug(f"  {node_type} next-step prediction loss: {node_loss.item():.4f}")
                
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
            val_hidden_state = None
            
            with torch.no_grad():
                for block in val_blocks:
                    if val_hidden_state:
                        val_hidden_state = {k: v.detach() for k, v in val_hidden_state.items()}
                    
                    recon_dict, embeddings, val_hidden_state = model(block["x_dict_seq"], block["edge_index_dict"], val_hidden_state)
                    
                    loss = 0
                    for node_type, recon_x in recon_dict.items():
                        if node_type in block["target_x_dict"]:
                            target_x = block["target_x_dict"][node_type]
                            node_loss = criterion(recon_x, target_x)
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
                # Log per-node-type validation losses
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
            
        logger.info(f"Saving model to {MODEL_SAVE_PATH}...")
        torch.save(model.state_dict(), MODEL_SAVE_PATH)
        
        # === NEW: Compute cluster statistics for anomaly detection ===
        logger.info("Computing healthy embedding cluster statistics...")
        from collections import defaultdict
        
        node_embeddings = defaultdict(lambda: defaultdict(list))
        
        with torch.no_grad():
            model.eval()
            for block in train_blocks:
                hidden_state = None
                if hidden_state:
                    hidden_state = {k: v.detach() for k, v in hidden_state.items()}
                    
                recon_dict, embeddings, hidden_state = model(block["x_dict_seq"], block["edge_index_dict"], hidden_state)
                
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
            upload_blob(GCS_BUCKET_NAME, MODEL_SAVE_PATH, f"models/stgnn/{MODEL_SAVE_PATH}")
            upload_blob(GCS_BUCKET_NAME, SCALER_PATH, f"models/stgnn/{SCALER_PATH}")
            upload_blob(GCS_BUCKET_NAME, STATS_SAVE_PATH, f"models/stgnn/{STATS_SAVE_PATH}")

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
