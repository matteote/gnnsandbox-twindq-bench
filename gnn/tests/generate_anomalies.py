import os
import pickle
import datetime
import logging
import json
from pathlib import Path
import sys

# Ensure the gnn/src directory is in the Python path
src_path = str((Path(__file__).resolve().parent.parent / "src").resolve())
if src_path not in sys.path:
    sys.path.insert(0, src_path)

from utils.data import SpannerDataset

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("anomaly_generator")

def generate_anomalies():
    # 1. Setup Configuration
    PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "agents-1234")
    INSTANCE_ID = os.getenv("SPANNER_INSTANCE", "networktopology-instance")
    DATABASE_ID = os.getenv("SPANNER_DATABASE", "networktopology-db")
    
    # Check for service account
    _local_creds = str((Path(__file__).resolve().parent.parent / "src" / "networkagent.json").resolve())
    if os.path.exists(_local_creds):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _local_creds
        logger.info(f"Using service-account credentials: {_local_creds}")

    output_dir = Path("anomaly_synthetic_data")
    output_dir.mkdir(exist_ok=True)

    # 2. Ingest real baseline data
    dataset = SpannerDataset(
        instance_id=INSTANCE_ID,
        database_id=DATABASE_ID,
        num_snapshots=5,
        interval_minutes=0.5,  # 30-second cadence — matches training data
        project_id=PROJECT_ID
    )

    logger.info("Fetching base snapshots from Spanner...")
    try:
        timestamps = dataset._get_timestamps()
        snapshots = [dataset.fetch_snapshot(ts) for ts in timestamps]
    except Exception as e:
        logger.error(f"Failed to fetch snapshots from Spanner: {e}")
        return

    if not snapshots:
        logger.error("No snapshots fetched. Cannot proceed.")
        return
    
    # 3. Inject Anomalies into the LATEST snapshot
    latest_snap = snapshots[-1]
    logger.info(f"Injecting anomalies into snapshot at {latest_snap['timestamp']}")

    # --- ANOMALY 1: CRC Error Gradient (Hardware Degradation) ---
    target_iface = next((n for n in latest_snap['nodes'] if n['type'] == 'interface'), None)
    if target_iface:
        logger.info(f"Injecting CRC Error Spike on Interface: {target_iface['id']} ({target_iface['name']})")
        target_iface['rx_errs_rate'] = 500.0 
        target_iface['state'] = 1.0
    
    # --- ANOMALY 2: CPU Spike (Resource Exhaustion) ---
    target_router = next((n for n in latest_snap['nodes'] if n['type'] == 'router' and n['role'] == 'PE'), None)
    if not target_router:
        target_router = next((n for n in latest_snap['nodes'] if n['type'] == 'router'), None)
        
    if target_router:
        logger.info(f"Injecting CPU Spike on Router: {target_router['id']} ({target_router['hostname']})")
        target_router['cpu'] = 8.0   # far outside training range (0.07–1.18)
        target_router['mem'] = 0.05  # far below baseline (always 1.0)

    # 4. Compute temporal features to "solidify" the anomalies (e.g. compute the gradient)
    SpannerDataset.compute_temporal_features(snapshots, interval_seconds=30.0)
    
    # Verify the gradient was captured
    if target_iface:
        logger.info(f"Computed rx_err_gradient for {target_iface['id']}: {target_iface.get('rx_err_gradient')}")

    # 5. Save snapshots as JSON
    for i, snap in enumerate(snapshots):
        filename = f"snapshot_{i:04d}.json"
        file_path = output_dir / filename
        with open(file_path, 'w') as f:
            json.dump(snap, f, indent=2)
        logger.info(f"Saved: {file_path}")

    logger.info(f"Successfully generated {len(snapshots)} JSON snapshots in '{output_dir}'")
    logger.info("Demo tip: The last snapshot in the sequence contains the synthetic anomalies.")

if __name__ == "__main__":
    generate_anomalies()
