# GNN Models — GCP Native Port Plan

**Status:** Planning  
**Decision:** Keep PyTorch + PyG models; modernise the operational layer to Vertex AI + Kubeflow Pipelines  
**Author:** Engineering  
**Last updated:** March 2026

---

## 1. Executive Summary

The GNN models (DGAT, HetGNN, STGNN) are currently trained and served as standalone Docker containers with no orchestration layer. Training is triggered manually by running Cloud Build, and inference runs as a background thread inside the serving container.

This document defines the plan to move to a fully GCP-native operational model:

- **Training** → Vertex AI Custom Training Jobs (custom containers with PyTorch + PyG)
- **Orchestration** → Vertex AI Pipelines (Kubeflow Pipelines v2) — one pipeline that trains all three models in parallel
- **Model versioning** → Vertex AI Model Registry
- **Online inference** → Vertex AI Endpoints (custom prediction containers)
- **Background/scheduled inference** → Cloud Run Jobs triggered by Cloud Scheduler
- **CI/CD** → Cloud Build triggers the KFP pipeline on commit to `main`

The PyTorch + PyG model code (`dgat.py`, `hetgnn.py`, `stgnn.py`) is **not changed** in this plan. The work is entirely in the infrastructure layer around those models.

---

## 2. Current Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     Current State                             │
│                                                              │
│  Cloud Build (manual trigger)                                │
│       │                                                      │
│       ▼                                                      │
│  Docker build → push to Artifact Registry                    │
│       │                                                      │
│       ├─ traingnn:latest  ─────────────────────────────────► │
│       │    runs train_dgat.py + train_hetgnn.py + train_stgnn│
│       │    sequentially in one container                     │
│       │    writes .pth + scalers to GCS manually             │
│       │                                                      │
│       └─ servegnn:latest ──────────────────────────────────► │
│            aiohttp server (serve.py)                         │
│            loads all 3 models from GCS on startup            │
│            runs inference every 60s in background thread     │
│            writes embeddings to Spanner                      │
│            exposes /predict and /health endpoints            │
└──────────────────────────────────────────────────────────────┘
```

**Current pain points:**

| Problem | Impact |
|---|---|
| Training runs sequentially in one container — no parallelism | 3× longer training wall time |
| No model versioning — every run overwrites GCS paths | Can't roll back to a previous model |
| No training run metadata (hyperparams, metrics, git SHA) | Impossible to reproduce a given model |
| No orchestration — manual trigger required for retraining | Stale models in production |
| Inference background thread couples training lifecycle to serving lifecycle | Serving container must restart to pick up new models |
| No hyperparameter search | Sub-optimal model quality |
| Single point of failure in serving container (inference + HTTP serving mixed) | Background inference crash takes down the HTTP endpoint |

---

## 3. Target Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         Target State                                      │
│                                                                          │
│  Cloud Build (on push to main)                                           │
│       │  builds + pushes images; submits pipeline to Vertex AI Pipelines │
│       ▼                                                                  │
│  ┌────────────────────────────────────────────────────────────────┐      │
│  │              Vertex AI Pipeline (KFP v2)                        │      │
│  │                                                                 │      │
│  │  [1] ingest_snapshots                                           │      │
│  │       Spanner → serialised snapshot dicts → GCS                 │      │
│  │         │                                                       │      │
│  │  [2] fit_scalers                                                │      │
│  │       fit sklearn scalers, save .pkl → GCS                      │      │
│  │         │                                                       │      │
│  │         ├──────────────────────────────────────┐               │      │
│  │         │                  │                   │               │      │
│  │  [3a] train_dgat    [3b] train_hetgnn   [3c] train_stgnn       │      │
│  │   Vertex AI Job      Vertex AI Job       Vertex AI Job         │      │
│  │   (PyTorch GPU)      (PyTorch GPU)       (PyTorch GPU)         │      │
│  │         │                  │                   │               │      │
│  │  [4a] eval_dgat    [4b] eval_hetgnn    [4c] eval_stgnn         │      │
│  │         │                  │                   │               │      │
│  │         └──────────────────┴─────────────────┘                │      │
│  │                            │                                   │      │
│  │  [5] register_models                                           │      │
│  │       Vertex AI Model Registry                                 │      │
│  │         │                                                       │      │
│  │  [6] deploy_endpoint  (conditional on val loss < threshold)    │      │
│  │       Vertex AI Endpoint (custom prediction container)         │      │
│  └────────────────────────────────────────────────────────────────┘      │
│                                                                          │
│  Cloud Scheduler (every 60s) ──► Cloud Run Job (inference)               │
│                                       reads latest models from GCS        │
│                                       fetches Spanner snapshot            │
│                                       runs HetGNN inference               │
│                                       writes embeddings to Spanner        │
│                                       exits (Cloud Run bills per-request) │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Repository Structure Changes

The existing `gnn/` directory gets the following additions. **Nothing in `gnn/src/model/` is changed.**

```
gnn/
├── Dockerfile.train              # EXISTING — keep for reference
├── Dockerfile.serve              # EXISTING — keep for reference  
├── Dockerfile.train.vertex       # NEW — PyTorch GPU base for Vertex AI Training
├── Dockerfile.serve.vertex       # NEW — custom prediction container for Vertex AI Endpoint
├── Dockerfile.infer.cloudrun     # NEW — lightweight inference container for Cloud Run Job
├── requirements.txt              # EXISTING — unchanged
├── requirements.vertex.txt       # NEW — adds kfp, google-cloud-aiplatform
├── cloudbuild.j2                 # EXISTING — updated to also submit KFP pipeline
│
├── pipeline/
│   ├── pipeline.py               # NEW — KFP v2 pipeline definition
│   ├── components/
│   │   ├── ingest.py             # NEW — KFP component: Spanner → GCS snapshots
│   │   ├── fit_scalers.py        # NEW — KFP component: fit sklearn scalers
│   │   ├── train.py              # NEW — KFP component: submits Vertex AI Training Job
│   │   ├── evaluate.py           # NEW — KFP component: loads model, computes val metrics
│   │   ├── register.py           # NEW — KFP component: Vertex AI Model Registry upload
│   │   └── deploy.py             # NEW — KFP component: Vertex AI Endpoint deployment
│   └── specs/
│       ├── dgat_job.yaml         # NEW — Vertex AI CustomJob spec for DGAT
│       ├── hetgnn_job.yaml       # NEW — Vertex AI CustomJob spec for HetGNN
│       └── stgnn_job.yaml        # NEW — Vertex AI CustomJob spec for STGNN
│
└── src/
    ├── train_dgat.py             # EXISTING — minor changes for Vertex AI env vars
    ├── train_hetgnn.py           # EXISTING — minor changes for Vertex AI env vars
    ├── train_stgnn.py            # EXISTING — minor changes for Vertex AI env vars
    ├── serve.py                  # EXISTING — refactored: background loop removed
    ├── infer.py                  # NEW — Cloud Run inference entry point
    ├── model/                    # UNCHANGED
    │   ├── dgat.py
    │   ├── hetgnn.py
    │   └── stgnn.py
    └── utils/                    # UNCHANGED
        ├── data.py
        └── gnn_utils.py
```

---

## 5. GCS Artefact Layout

Current GCS layout (flat per-model):
```
gs://network-model-artifacts/
  models/dgat/dgat_model.pth
  models/dgat/dgat_scalers.pkl
  models/dgat/dgat_model_stats.pth
  models/hetgnn/...
  models/stgnn/...
```

New layout (versioned by pipeline run ID):
```
gs://network-model-artifacts/
  snapshots/
    {pipeline_run_id}/
      snapshot_{timestamp}.pkl     ← serialised snapshot dicts
  scalers/
    {pipeline_run_id}/
      scalers.pkl                  ← shared sklearn scalers
  models/
    dgat/
      {pipeline_run_id}/
        model.pth
        model_stats.pth
        metadata.json              ← hyperparams, val loss, git SHA, timestamp
    hetgnn/
      {pipeline_run_id}/
        model.pth
        model_stats.pth
        metadata.json
    stgnn/
      {pipeline_run_id}/
        model.pth
        model_stats.pth
        metadata.json
  latest/                          ← symlinked by register_models component
    dgat → models/dgat/{best_run_id}/
    hetgnn → models/hetgnn/{best_run_id}/
    stgnn → models/stgnn/{best_run_id}/
```

The `latest/` pointers are GCS object copies (GCS has no symlinks). The `register_models` component writes a `latest_run.json` manifest file that the Cloud Run inference container reads to find the current best model artefacts.

---

## 6. Kubeflow Pipeline — Component Specifications

### 6.1 Component: `ingest_snapshots`

**What it does:**  
Instantiates `SpannerDataset`, fetches N snapshots from Spanner, serialises each as a Python dict using `pickle`, and writes them to GCS. This decouples Spanner access from the training jobs — training jobs read from GCS, not Spanner directly.

**Inputs:**
- `spanner_instance: str`
- `spanner_database: str`
- `num_snapshots: int` (default 100)
- `interval_minutes: int` (default 1)
- `gcs_bucket: str`
- `run_id: str` (pipeline run ID for path namespacing)

**Outputs:**
- `snapshots_gcs_path: str` — path to the directory of `.pkl` files

**Resource requirements:** `e2-standard-4`, no GPU, 15-minute timeout

---

### 6.2 Component: `fit_scalers`

**What it does:**  
Reads serialised snapshot dicts from GCS, instantiates `GraphBuilder`, calls `gb.fit_scalers()`, and saves `scalers.pkl` to GCS.

**Inputs:**
- `snapshots_gcs_path: str`
- `gcs_bucket: str`
- `run_id: str`

**Outputs:**
- `scalers_gcs_path: str`

**Resource requirements:** `e2-standard-4`, no GPU

---

### 6.3 Components: `train_dgat`, `train_hetgnn`, `train_stgnn`

These three components run in **parallel** (no dependency on each other, both depend on `fit_scalers`). Each component submits a **Vertex AI Custom Training Job** using `google_cloud_pipeline_components.v1.custom_job.CustomTrainingJobOp`.

**Inputs:**
- `snapshots_gcs_path: str`
- `scalers_gcs_path: str`
- `gcs_bucket: str`
- `run_id: str`
- `model_name: str` (`dgat` / `hetgnn` / `stgnn`)
- `machine_type: str` (default `n1-standard-8`)
- `accelerator_type: str` (default `NVIDIA_TESLA_T4`)
- `accelerator_count: int` (default `1`)

**Outputs:**
- `model_gcs_path: str` — path to trained `.pth` + stats file

**Training container:** `{REGION}-docker.pkg.dev/{PROJECT}/{REPO}/traingnn-vertex:latest`  
The container entrypoint is the relevant training script (`train_dgat.py`, etc.), now reading `SNAPSHOTS_GCS_PATH` and `SCALERS_GCS_PATH` env vars instead of connecting to Spanner directly.

**Vertex AI Training Job environment variables:**
```
SPANNER_INSTANCE={spanner_instance}
SPANNER_DATABASE={spanner_database}
GCS_BUCKET_NAME={gcs_bucket}
SNAPSHOTS_GCS_PATH={snapshots_gcs_path}
SCALERS_GCS_PATH={scalers_gcs_path}
MODEL_OUTPUT_PATH=gs://{gcs_bucket}/models/{model_name}/{run_id}/
AIP_MODEL_DIR=gs://{gcs_bucket}/models/{model_name}/{run_id}/
```

**Machine spec (configurable per pipeline run):**
```yaml
machineSpec:
  machineType: n1-standard-8
  acceleratorType: NVIDIA_TESLA_T4
  acceleratorCount: 1
diskSpec:
  bootDiskType: pd-ssd
  bootDiskSizeGb: 100
```

---

### 6.4 Components: `evaluate_dgat`, `evaluate_hetgnn`, `evaluate_stgnn`

**What it does:**  
Loads the trained model from GCS, runs it on a held-out validation set (last 20% of snapshots by timestamp), and produces metrics.

**Inputs:**
- `model_gcs_path: str`
- `snapshots_gcs_path: str`
- `scalers_gcs_path: str`
- `model_name: str`

**Outputs (KFP Metrics artifacts):**
- `val_loss: float`
- `best_node_type_losses: dict` — per-node-type val loss breakdown
- `metrics_artifact: Output[Metrics]` — written to Vertex AI Experiments for tracking

**Resource requirements:** `n1-standard-4` with T4 GPU

---

### 6.5 Component: `register_models`

**What it does:**  
Uploads all three trained models to Vertex AI Model Registry. Attaches metadata (val loss, hyperparams, git SHA, pipeline run ID, timestamp). Writes the `latest_run.json` manifest to GCS.

**Inputs:**
- `dgat_model_gcs_path: str`
- `hetgnn_model_gcs_path: str`
- `stgnn_model_gcs_path: str`
- `dgat_val_loss: float`
- `hetgnn_val_loss: float`
- `stgnn_val_loss: float`
- `run_id: str`

**Outputs:**
- `dgat_model_resource_name: str` — Vertex AI Model resource name
- `hetgnn_model_resource_name: str`
- `stgnn_model_resource_name: str`

**Model Registry entry metadata:**
```json
{
  "run_id": "{pipeline_run_id}",
  "git_sha": "{git_sha}",
  "trained_at": "{iso_timestamp}",
  "val_loss_dgat": 0.0142,
  "val_loss_hetgnn": 0.0231,
  "val_loss_stgnn": 0.0087,
  "hidden_channels": 64,
  "num_heads": 4,
  "num_layers": 2,
  "spanner_instance": "networktopology-instance",
  "spanner_database": "networktopology-db"
}
```

---

### 6.6 Component: `deploy_endpoint` (conditional)

**What it does:**  
Deploys or updates the Vertex AI Endpoint with the latest trained models. This step is **conditional** — it only runs if all three models' val losses are below their respective thresholds (configurable pipeline parameter). This prevents a degraded model from being auto-deployed.

**Condition:** `dgat_val_loss < MAX_DGAT_VAL_LOSS and hetgnn_val_loss < MAX_HETGNN_VAL_LOSS and stgnn_val_loss < MAX_STGNN_VAL_LOSS`

**Inputs:**
- `dgat_model_resource_name: str`
- `hetgnn_model_resource_name: str`
- `stgnn_model_resource_name: str`
- `endpoint_resource_name: str` (existing endpoint, or empty to create new)

**Outputs:**
- `endpoint_resource_name: str`
- `deployed_model_ids: list[str]`

---

## 7. Pipeline DAG — Full Definition

```python
# gnn/pipeline/pipeline.py (KFP SDK v2)

@pipeline(
    name="gnn-training-pipeline",
    description="Trains DGAT, HetGNN, and STGNN in parallel on Vertex AI",
    pipeline_root=PIPELINE_ROOT,
)
def gnn_training_pipeline(
    spanner_instance: str = "networktopology-instance",
    spanner_database: str = "networktopology-db",
    gcs_bucket: str = "network-model-artifacts",
    num_snapshots: int = 100,
    interval_minutes: int = 1,
    machine_type: str = "n1-standard-8",
    accelerator_type: str = "NVIDIA_TESLA_T4",
    max_dgat_val_loss: float = 1.0,
    max_hetgnn_val_loss: float = 1.0,
    max_stgnn_val_loss: float = 1.0,
    endpoint_resource_name: str = "",
):
    run_id = dsl.PIPELINE_JOB_ID_PLACEHOLDER

    ingest_task = ingest_snapshots(
        spanner_instance=spanner_instance,
        spanner_database=spanner_database,
        num_snapshots=num_snapshots,
        interval_minutes=interval_minutes,
        gcs_bucket=gcs_bucket,
        run_id=run_id,
    )

    scalers_task = fit_scalers(
        snapshots_gcs_path=ingest_task.outputs["snapshots_gcs_path"],
        gcs_bucket=gcs_bucket,
        run_id=run_id,
    )

    # --- Parallel training ---
    dgat_train = train_model(
        model_name="dgat",
        snapshots_gcs_path=ingest_task.outputs["snapshots_gcs_path"],
        scalers_gcs_path=scalers_task.outputs["scalers_gcs_path"],
        gcs_bucket=gcs_bucket,
        run_id=run_id,
        machine_type=machine_type,
        accelerator_type=accelerator_type,
    )

    hetgnn_train = train_model(
        model_name="hetgnn",
        snapshots_gcs_path=ingest_task.outputs["snapshots_gcs_path"],
        scalers_gcs_path=scalers_task.outputs["scalers_gcs_path"],
        gcs_bucket=gcs_bucket,
        run_id=run_id,
        machine_type=machine_type,
        accelerator_type=accelerator_type,
    )

    stgnn_train = train_model(
        model_name="stgnn",
        snapshots_gcs_path=ingest_task.outputs["snapshots_gcs_path"],
        scalers_gcs_path=scalers_task.outputs["scalers_gcs_path"],
        gcs_bucket=gcs_bucket,
        run_id=run_id,
        machine_type=machine_type,
        accelerator_type=accelerator_type,
    )

    # --- Parallel evaluation ---
    dgat_eval = evaluate_model(
        model_name="dgat",
        model_gcs_path=dgat_train.outputs["model_gcs_path"],
        snapshots_gcs_path=ingest_task.outputs["snapshots_gcs_path"],
        scalers_gcs_path=scalers_task.outputs["scalers_gcs_path"],
    )

    hetgnn_eval = evaluate_model(
        model_name="hetgnn",
        model_gcs_path=hetgnn_train.outputs["model_gcs_path"],
        snapshots_gcs_path=ingest_task.outputs["snapshots_gcs_path"],
        scalers_gcs_path=scalers_task.outputs["scalers_gcs_path"],
    )

    stgnn_eval = evaluate_model(
        model_name="stgnn",
        model_gcs_path=stgnn_train.outputs["model_gcs_path"],
        snapshots_gcs_path=ingest_task.outputs["snapshots_gcs_path"],
        scalers_gcs_path=scalers_task.outputs["scalers_gcs_path"],
    )

    # --- Register all three ---
    register_task = register_models(
        dgat_model_gcs_path=dgat_train.outputs["model_gcs_path"],
        hetgnn_model_gcs_path=hetgnn_train.outputs["model_gcs_path"],
        stgnn_model_gcs_path=stgnn_train.outputs["model_gcs_path"],
        dgat_val_loss=dgat_eval.outputs["val_loss"],
        hetgnn_val_loss=hetgnn_eval.outputs["val_loss"],
        stgnn_val_loss=stgnn_eval.outputs["val_loss"],
        run_id=run_id,
    )

    # --- Conditional deployment ---
    with dsl.Condition(
        (dgat_eval.outputs["val_loss"] < max_dgat_val_loss) &
        (hetgnn_eval.outputs["val_loss"] < max_hetgnn_val_loss) &
        (stgnn_eval.outputs["val_loss"] < max_stgnn_val_loss),
        name="quality-gate",
    ):
        deploy_endpoint(
            dgat_model_resource_name=register_task.outputs["dgat_model_resource_name"],
            hetgnn_model_resource_name=register_task.outputs["hetgnn_model_resource_name"],
            stgnn_model_resource_name=register_task.outputs["stgnn_model_resource_name"],
            endpoint_resource_name=endpoint_resource_name,
        )
```

---

## 8. Training Script Changes (Minimal)

The existing training scripts (`train_dgat.py`, `train_hetgnn.py`, `train_stgnn.py`) need **only two changes**:

### 8.1 Data loading path

Currently each script instantiates `SpannerDataset` directly. In the new flow, the `ingest_snapshots` pipeline component has already fetched and serialised snapshots to GCS. Training scripts should check for the `SNAPSHOTS_GCS_PATH` env var and load from GCS if set, otherwise fall back to live Spanner queries (for backward compatibility / local dev).

```python
# Add near the top of each train_*.py, before run_training_pipeline()

SNAPSHOTS_GCS_PATH = os.getenv("SNAPSHOTS_GCS_PATH", "")
SCALERS_GCS_PATH = os.getenv("SCALERS_GCS_PATH", "")
MODEL_OUTPUT_PATH = os.getenv("MODEL_OUTPUT_PATH", "")

def load_snapshots_from_gcs(gcs_path: str) -> list:
    """Downloads serialised snapshot dicts from GCS and returns list of dicts."""
    storage_client = storage.Client()
    # gcs_path = gs://bucket/snapshots/{run_id}/
    bucket_name, prefix = gcs_path.replace("gs://", "").split("/", 1)
    bucket = storage_client.bucket(bucket_name)
    blobs = list(bucket.list_blobs(prefix=prefix))
    snapshots = []
    for blob in sorted(blobs, key=lambda b: b.name):
        if blob.name.endswith(".pkl"):
            data = pickle.loads(blob.download_as_bytes())
            snapshots.append(data)
    return snapshots
```

### 8.2 Model output path

Currently models save to the current working directory. Vertex AI mounts `AIP_MODEL_DIR` as the canonical output directory. Add at the end of each training script:

```python
# After saving locally, if AIP_MODEL_DIR is set, copy to Vertex AI output dir
AIP_MODEL_DIR = os.getenv("AIP_MODEL_DIR", "")
if AIP_MODEL_DIR:
    upload_blob(GCS_BUCKET_NAME, MODEL_SAVE_PATH, 
                f"{AIP_MODEL_DIR.replace('gs://' + GCS_BUCKET_NAME + '/', '')}{MODEL_SAVE_PATH}")
```

---

## 9. Serving — Vertex AI Endpoint (Custom Prediction Container)

### 9.1 What stays the same

- All model loading logic (`load_models()`)
- Anomaly scoring (`compute_anomaly_scores()`, `compute_mahalanobis_distance()`)
- Feature attribution (`compute_feature_attribution()`)
- The `/predict` and `/health` HTTP routes

### 9.2 What changes

**Remove** the `background_inference_loop()` and all `asyncio` task management. The aiohttp server becomes a pure synchronous prediction endpoint — it receives a request, runs inference, returns a response. It does **not** poll Spanner on a schedule.

The Vertex AI Endpoint reads `AIP_PREDICT_ROUTE`, `AIP_HEALTH_ROUTE`, and `AIP_HTTP_PORT` env vars (already handled in the current `serve.py`).

`Dockerfile.serve.vertex`:
```dockerfile
FROM python:3.13.2-slim
ENV SPANNER_INSTANCE=networktopology-instance
ENV SPANNER_DATABASE=networktopology-db
ENV GCS_BUCKET_NAME=network-model-artifacts
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
ADD gnn/requirements.txt /
RUN pip install -r /requirements.txt
ADD gnn/src/ /app
WORKDIR /app
CMD ["python", "serve.py"]
```

### 9.3 Vertex AI Endpoint configuration

```yaml
# Endpoint: gnn-inference-endpoint
dedicatedResources:
  machineSpec:
    machineType: n1-standard-4
  minReplicaCount: 1
  maxReplicaCount: 3   # autoscale up to 3 replicas under load
trafficSplit:
  "0": 100             # 100% to latest deployed model
```

---

## 10. Background Inference — Cloud Run Job

The 60-second background inference loop moves from a thread inside `serve.py` to a **Cloud Run Job** triggered by Cloud Scheduler.

### 10.1 New file: `gnn/src/infer.py`

Extracted from `serve.py` — contains `run_inference()` and all dependencies. Entry point is a simple `main()` that calls `run_inference()` once and exits. Cloud Run Jobs handle the scheduling; the code itself is stateless.

```python
# gnn/src/infer.py
if __name__ == "__main__":
    load_models()
    result = asyncio.run(run_inference())
    if "error" in result:
        sys.exit(1)
    logger.info(f"Inference complete: {result['anomaly_count']} anomalies detected")
    sys.exit(0)
```

### 10.2 `Dockerfile.infer.cloudrun`

```dockerfile
FROM python:3.13.2-slim
ENV SPANNER_INSTANCE=networktopology-instance
ENV SPANNER_DATABASE=networktopology-db
ENV GCS_BUCKET_NAME=network-model-artifacts
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
ADD gnn/requirements.txt /
RUN pip install -r /requirements.txt
ADD gnn/src/ /app
WORKDIR /app
CMD ["python", "infer.py"]
```

### 10.3 Cloud Run Job spec

```yaml
apiVersion: run.googleapis.com/v1
kind: Job
metadata:
  name: gnn-inference
  namespace: default
spec:
  template:
    spec:
      template:
        spec:
          containers:
          - image: {REGION}-docker.pkg.dev/{PROJECT}/{REPO}/infergnn:latest
            env:
            - name: SPANNER_INSTANCE
              value: networktopology-instance
            - name: SPANNER_DATABASE
              value: networktopology-db
            - name: GCS_BUCKET_NAME
              value: network-model-artifacts
            resources:
              limits:
                cpu: "2"
                memory: 4Gi
          serviceAccountName: networkagent@{PROJECT}.iam.gserviceaccount.com
          maxRetries: 2
          timeoutSeconds: 120
```

### 10.4 Cloud Scheduler trigger

```yaml
# Cloud Scheduler job — runs inference every 60 seconds
# Note: Cloud Scheduler minimum granularity is 1 minute
name: gnn-inference-trigger
schedule: "* * * * *"      # every minute
timeZone: UTC
target:
  httpTarget:
    uri: https://run.googleapis.com/apis/run.googleapis.com/v1/namespaces/{PROJECT}/jobs/gnn-inference:run
    httpMethod: POST
    oauthToken:
      serviceAccountEmail: networkagent@{PROJECT}.iam.gserviceaccount.com
```

---

## 11. CI/CD — Updated Cloud Build

`gnn/cloudbuild.j2` updated to build three images and submit the KFP pipeline:

```yaml
steps:
# Build training image
- name: 'gcr.io/cloud-builders/docker'
  script: |
    docker build -t {{GOOGLE_REGION}}-docker.pkg.dev/{{GOOGLE_PROJECT}}/{{GOOGLE_REPO}}/traingnn-vertex:latest \
      -f gnn/Dockerfile.train.vertex .
  automapSubstitutions: true

# Build serving image
- name: 'gcr.io/cloud-builders/docker'
  script: |
    docker build -t {{GOOGLE_REGION}}-docker.pkg.dev/{{GOOGLE_PROJECT}}/{{GOOGLE_REPO}}/servegnn-vertex:latest \
      -f gnn/Dockerfile.serve.vertex .
  automapSubstitutions: true

# Build inference Cloud Run image
- name: 'gcr.io/cloud-builders/docker'
  script: |
    docker build -t {{GOOGLE_REGION}}-docker.pkg.dev/{{GOOGLE_PROJECT}}/{{GOOGLE_REPO}}/infergnn:latest \
      -f gnn/Dockerfile.infer.cloudrun .
  automapSubstitutions: true

# Push images
- name: 'gcr.io/cloud-builders/docker'
  script: |
    docker push {{GOOGLE_REGION}}-docker.pkg.dev/{{GOOGLE_PROJECT}}/{{GOOGLE_REPO}}/traingnn-vertex:latest
    docker push {{GOOGLE_REGION}}-docker.pkg.dev/{{GOOGLE_PROJECT}}/{{GOOGLE_REPO}}/servegnn-vertex:latest
    docker push {{GOOGLE_REGION}}-docker.pkg.dev/{{GOOGLE_PROJECT}}/{{GOOGLE_REPO}}/infergnn:latest
  automapSubstitutions: true

# Submit KFP pipeline to Vertex AI Pipelines
- name: 'python:3.12-slim'
  script: |
    pip install kfp google-cloud-aiplatform -q
    python gnn/pipeline/submit_pipeline.py \
      --project={{GOOGLE_PROJECT}} \
      --region={{GOOGLE_REGION}} \
      --pipeline-root=gs://network-model-artifacts/pipeline-root
  automapSubstitutions: true

images:
- '{{GOOGLE_REGION}}-docker.pkg.dev/{{GOOGLE_PROJECT}}/{{GOOGLE_REPO}}/traingnn-vertex:latest'
- '{{GOOGLE_REGION}}-docker.pkg.dev/{{GOOGLE_PROJECT}}/{{GOOGLE_REPO}}/servegnn-vertex:latest'
- '{{GOOGLE_REGION}}-docker.pkg.dev/{{GOOGLE_PROJECT}}/{{GOOGLE_REPO}}/infergnn:latest'
```

---

## 12. IAM Requirements

The `networkagent` service account needs the following roles (most already granted):

| Role | Already granted | Needed for |
|---|---|---|
| `roles/spanner.databaseReader` | Yes | `ingest_snapshots` component, Cloud Run inference |
| `roles/storage.objectAdmin` | Yes | Reading/writing model artefacts, snapshots |
| `roles/aiplatform.user` | **New** | Submitting Vertex AI Training Jobs + Pipelines |
| `roles/aiplatform.modelUser` | **New** | Vertex AI Model Registry + Endpoint deployment |
| `roles/run.invoker` | **New** | Cloud Scheduler → Cloud Run Job trigger |
| `roles/logging.logWriter` | Yes | Cloud Run + Vertex AI job logging |

---

## 13. Hyperparameter Tuning with Vertex AI Vizier

Each training component can optionally run with hyperparameter tuning enabled. When `enable_hparam_tuning=True` is passed to the pipeline, the training components wrap the Vertex AI Training Job in a `HyperparameterTuningJob` instead of a `CustomJob`.

**DGAT search space:**

| Parameter | Type | Range |
|---|---|---|
| `hidden_channels` | `DISCRETE` | 32, 64, 128 |
| `num_heads` | `DISCRETE` | 2, 4, 8 |
| `num_layers` | `INTEGER` | 2, 3 |
| `learning_rate` | `DOUBLE` | 1e-4 to 1e-2, log scale |

**HetGNN search space:**

| Parameter | Type | Range |
|---|---|---|
| `hidden_channels` | `DISCRETE` | 32, 64, 128 |
| `alpha` (config loss weight) | `DOUBLE` | 0.2 to 0.6 |
| `beta` (protocol loss weight) | `DOUBLE` | 0.2 to 0.6 |
| `diversity_weight` | `DOUBLE` | 0.05 to 0.2 |

**STGNN search space:**

| Parameter | Type | Range |
|---|---|---|
| `hidden_channels` | `DISCRETE` | 32, 64, 128 |
| `temporal_steps` | `INTEGER` | 6, 12, 24 |
| `rnn_type` | `CATEGORICAL` | gru, lstm |
| `learning_rate` | `DOUBLE` | 1e-4 to 1e-2, log scale |

**Metric to minimise:** `val_loss` (reported via `hypertune` library from within the training script).

---

## 14. Vertex AI Experiments Integration

Each pipeline run creates a Vertex AI Experiment run with:
- **Parameters:** All hyperparameters (hidden_channels, num_heads, learning_rate, etc.)
- **Metrics:** `val_loss`, `best_epoch`, per-node-type loss breakdown
- **Artefacts:** Link to model GCS path, scalers GCS path, pipeline run ID
- **Tags:** git SHA, environment (dev/staging/prod)

This enables loss curves, model comparison across runs, and full audit trail via the Vertex AI UI.

---

## 15. Migration Phases

### Phase 1 — Pipeline Infrastructure (Weeks 1–2)
- [ ] Create `gnn/pipeline/` directory and KFP component skeletons
- [ ] Implement `ingest_snapshots` component (Spanner → GCS pickles)
- [ ] Implement `fit_scalers` component (reads pickles, fits sklearn scalers)
- [ ] Update training scripts to accept `SNAPSHOTS_GCS_PATH` + `MODEL_OUTPUT_PATH` env vars
- [ ] Create `Dockerfile.train.vertex` with PyTorch + CUDA base image
- [ ] Test end-to-end: run pipeline locally with KFP local runner

### Phase 2 — Training Jobs on Vertex AI (Week 3)
- [ ] Implement `train_model` KFP component (submits `CustomTrainingJobOp`)
- [ ] Write Vertex AI job specs (`dgat_job.yaml`, `hetgnn_job.yaml`, `stgnn_job.yaml`)
- [ ] Implement `evaluate_model` component
- [ ] Test full training pipeline on Vertex AI with 24 snapshots (smoke test)
- [ ] Verify model artefacts land in versioned GCS paths

### Phase 3 — Model Registry + Endpoint (Week 4)
- [ ] Implement `register_models` component
- [ ] Implement `deploy_endpoint` component with quality gate condition
- [ ] Create `Dockerfile.serve.vertex` (serve.py without background loop)
- [ ] Deploy Vertex AI Endpoint with custom prediction container
- [ ] Verify `/predict` and `/health` routes respond correctly

### Phase 4 — Cloud Run Inference Job (Week 4–5)
- [ ] Create `gnn/src/infer.py` (extracted from serve.py)
- [ ] Create `Dockerfile.infer.cloudrun`
- [ ] Deploy Cloud Run Job
- [ ] Configure Cloud Scheduler trigger (every 60 seconds)
- [ ] Verify Spanner embeddings are being written correctly
- [ ] Decommission background thread in serve.py

### Phase 5 — CI/CD + Scheduling (Week 5)
- [ ] Update `gnn/cloudbuild.j2` with new image builds + pipeline submission step
- [ ] Create `gnn/pipeline/submit_pipeline.py` helper script
- [ ] Add Cloud Build trigger on `main` branch for the `gnn/` path
- [ ] Add Cloud Scheduler job for weekly retraining pipeline run
- [ ] End-to-end test: push commit → Cloud Build → KFP pipeline → Vertex AI Training → Model Registry → Endpoint update

### Phase 6 — Hardening (Week 6)
- [ ] Enable Vertex AI Vizier hyperparameter tuning for one model (start with HetGNN)
- [ ] Add Vertex AI Experiments logging to training scripts
- [ ] Set up pipeline failure alerting (Cloud Monitoring → alerting policy on failed pipeline runs)
- [ ] Document runbook: how to manually trigger retraining, roll back to previous model, inspect training metrics
- [ ] Load test Vertex AI Endpoint at 10× expected QPS

---

## 16. `install.sh` Changes

The existing `install.sh` requires changes in six functions to support the new Vertex AI Pipelines + Cloud Run inference architecture. All changes are additive and backward compatible — the existing `DeployGNN` flag (`--deploy gnn`) still works but now drives the new pipeline-based flow.

---

### 16.1 `CheckGCPEnv()` — Add KFP / AI Platform SDK check

Add a check that the `kfp` and `google-cloud-aiplatform` Python packages are available, as the Cloud Build pipeline submission step (`gnn/pipeline/submit_pipeline.py`) requires them locally when compiling the pipeline YAML. The check is a warning, not a hard exit, since Cloud Build itself has them installed via the inline `pip install` step.

```bash
# Add after the existing jinja/ansible checks:

# test if kfp python package is available (needed for local pipeline compilation)
if ! python3 -c "import kfp" &> /dev/null; then
    echo "WARNING: kfp package not found locally. Install with: pip install kfp google-cloud-aiplatform"
    echo "  This is only required for local pipeline testing; Cloud Build installs it automatically."
fi
```

---

### 16.2 `SetDemoEnv()` — Add new environment variables

Add two new exported variables used by the pipeline and Cloud Run inference components:

```bash
# Add after the existing GOOGLE_SPANNER_DATABASE export:

export GOOGLE_GNN_BUCKET="network-model-artifacts"
export GOOGLE_PIPELINE_ROOT="gs://network-model-artifacts/pipeline-root"
export GOOGLE_INFERENCE_JOB_NAME="gnn-inference"
export GOOGLE_SCHEDULER_JOB_NAME="gnn-inference-trigger"
```

---

### 16.3 `Create()` — New API, IAM, and credential copy changes

**New GCP APIs to enable** (add after the existing `gcloud services enable` block):

```bash
# Vertex AI Pipelines (Kubeflow)
gcloud services enable --project=$GOOGLE_PROJECT ml.googleapis.com
# Cloud Scheduler (for triggering Cloud Run inference job)
gcloud services enable --project=$GOOGLE_PROJECT cloudscheduler.googleapis.com
```

> Note: `aiplatform.googleapis.com` and `run.googleapis.com` are already enabled in the existing script.

**New Cloud Build service account IAM bindings** (add to the Cloud Build SA grants loop):

The Cloud Build SA (`{PROJECT_NUMBER}-compute@developer.gserviceaccount.com`) needs to submit Vertex AI Pipeline jobs from the Cloud Build `submit_pipeline.py` step:

```bash
# In the loop granting roles to CLOUD_BUILD_COMPUTE_SVC_ACCOUNT, add:
"roles/aiplatform.user"          # submit KFP pipeline jobs
"roles/storage.objectAdmin"      # read/write pipeline root + snapshot GCS paths
```

**Updated full loop:**
```bash
for role in "roles/storage.objectUser" "roles/logging.logWriter" \
            "roles/artifactregistry.writer" "roles/cloudbuild.builds.builder" \
            "roles/aiplatform.user" "roles/storage.objectAdmin"; do
    echo "$role"
    gcloud projects add-iam-policy-binding $GOOGLE_PROJECT \
      --member="serviceAccount:$CLOUD_BUILD_COMPUTE_SVC_ACCOUNT" \
      --role="$role" --no-user-output-enabled
done
```

**New `networkagent` service account IAM roles** (in the new SA creation block — these are additions to the existing list):

```bash
# Add to the existing roles loop for GOOGLE_SERVICE_ACCOUNT:
"roles/aiplatform.modelUser"     # Vertex AI Model Registry read/write
"roles/cloudscheduler.admin"     # create/update Cloud Scheduler jobs
```

The full updated list in the `for role in ...` block becomes:
```bash
for role in "roles/editor" "roles/container.admin" "roles/compute.admin" \
    "roles/compute.networkAdmin" "roles/iam.serviceAccountAdmin" \
    "roles/monitoring.metricWriter" \
    "roles/aiplatform.user" "roles/aiplatform.admin" "roles/aiplatform.modelUser" \
    "roles/logging.logWriter" "roles/run.admin" "roles/spanner.databaseUser" \
    "roles/pubsub.editor" "roles/pubsub.subscriber" "roles/monitoring.viewer" \
    "roles/cloudscheduler.admin"; do
```

**New `networkagent.json` copy target** — add the Cloud Run inference src directory:

```bash
# In the networkagent.json copy block, this path is already covered:
cp networkagent.json gnn/src    # ← covers both serve.py and infer.py (same directory)
```

No additional copy needed since `infer.py` lives in `gnn/src/`.

**New Jinja template generation** — add pipeline submit script templating:

```bash
# Add to the "generating networkagent and operator yaml files" block:
jinja -E GOOGLE_PROJECT -E GOOGLE_REGION -E GOOGLE_REPO -E GOOGLE_GNN_BUCKET \
      -E GOOGLE_PIPELINE_ROOT gnn/pipeline/submit_pipeline.j2 > gnn/pipeline/submit_pipeline.py
```

---

### 16.4 `Start()` — Create pipeline-root GCS path and Vertex AI Pipelines bucket folder

Add after the existing GCS bucket creation block:

```bash
# Create pipeline root folder in GCS bucket (Vertex AI Pipelines requires this to exist)
echo "###################################"
echo "Initialising Vertex AI Pipeline root"
echo "###################################"
gcloud storage objects describe gs://network-model-artifacts/pipeline-root/.keep > /dev/null 2>&1
if [[ $? -ne 0 ]]; then
    echo "Creating pipeline-root folder in GCS bucket..."
    echo "" | gcloud storage cp - gs://network-model-artifacts/pipeline-root/.keep
fi

# Create snapshots and models folder structure
for folder in "snapshots" "scalers" "models/dgat" "models/hetgnn" "models/stgnn" "models/latest"; do
    gcloud storage objects describe gs://network-model-artifacts/${folder}/.keep > /dev/null 2>&1
    if [[ $? -ne 0 ]]; then
        echo "" | gcloud storage cp - gs://network-model-artifacts/${folder}/.keep
    fi
done
```

---

### 16.5 `DeployGNN()` — Full replacement of function body

The current `DeployGNN()` function does a direct `gcloud ai custom-jobs create` with the training image. This is replaced by the full KFP pipeline flow: build three images, submit the pipeline, and deploy the Cloud Run inference job.

**Replace the current `DeployGNN()` function body with:**

```bash
DeployGNN()
{
    export GOOGLE_SERVICE_ACCOUNT=`gcloud iam service-accounts list --format="value(email)" --filter="networkagent@${GOOGLE_PROJECT}."`

    # -----------------------------------------------
    # Step 1: Build and push all three GNN images
    # -----------------------------------------------
    TRAIN_IMAGE_URI="$GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/$GOOGLE_REPO/traingnn-vertex:latest"
    SERVE_IMAGE_URI="$GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/$GOOGLE_REPO/servegnn-vertex:latest"
    INFER_IMAGE_URI="$GOOGLE_REGION-docker.pkg.dev/$GOOGLE_PROJECT/$GOOGLE_REPO/infergnn:latest"

    if [[ $YES_FLAG != "y" ]] && [[ $NO_FLAG != "y" ]] && \
       $(gcloud artifacts docker images describe $TRAIN_IMAGE_URI >/dev/null 2>&1); then
        read -p "GNN images already exist. Rebuild? (y/n) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            gcloud builds submit --region=$GOOGLE_REGION --config=gnn/cloudbuild.yaml .
        fi
    elif [[ $NO_FLAG == "y" ]] && $(gcloud artifacts docker images describe $TRAIN_IMAGE_URI >/dev/null 2>&1); then
        echo "GNN images already exist - not rebuilding (NO_FLAG set)"
    else
        gcloud builds submit --region=$GOOGLE_REGION --config=gnn/cloudbuild.yaml .
    fi

    # -----------------------------------------------
    # Step 2: Submit KFP training pipeline
    # -----------------------------------------------
    echo "#################################################"
    echo "Submitting GNN training pipeline to Vertex AI..."
    echo "#################################################"
    pip install kfp google-cloud-aiplatform --quiet
    python3 gnn/pipeline/submit_pipeline.py \
        --project=$GOOGLE_PROJECT \
        --region=$GOOGLE_REGION \
        --pipeline-root=$GOOGLE_PIPELINE_ROOT \
        --spanner-instance=$GOOGLE_SPANNER_INSTANCE \
        --spanner-database=$GOOGLE_SPANNER_DATABASE \
        --gcs-bucket=$GOOGLE_GNN_BUCKET

    echo "Pipeline submitted. Monitor progress at:"
    echo "  https://console.cloud.google.com/vertex-ai/pipelines?project=$GOOGLE_PROJECT"

    # -----------------------------------------------
    # Step 3: Deploy the Cloud Run inference Job
    # -----------------------------------------------
    echo "####################################"
    echo "Deploying GNN inference Cloud Run Job"
    echo "####################################"

    gcloud run jobs describe $GOOGLE_INFERENCE_JOB_NAME --region=$GOOGLE_REGION > /dev/null 2>&1
    if [[ $? -ne 0 ]]; then
        echo "Creating Cloud Run Job '$GOOGLE_INFERENCE_JOB_NAME'..."
        gcloud run jobs create $GOOGLE_INFERENCE_JOB_NAME \
            --image=$INFER_IMAGE_URI \
            --region=$GOOGLE_REGION \
            --service-account=$GOOGLE_SERVICE_ACCOUNT \
            --memory=4Gi \
            --cpu=2 \
            --max-retries=2 \
            --task-timeout=120 \
            --set-env-vars="SPANNER_INSTANCE=$GOOGLE_SPANNER_INSTANCE" \
            --set-env-vars="SPANNER_DATABASE=$GOOGLE_SPANNER_DATABASE" \
            --set-env-vars="GCS_BUCKET_NAME=$GOOGLE_GNN_BUCKET" \
            --set-env-vars="GOOGLE_APPLICATION_CREDENTIALS=/app/networkagent.json"
    else
        echo "Updating existing Cloud Run Job '$GOOGLE_INFERENCE_JOB_NAME'..."
        gcloud run jobs update $GOOGLE_INFERENCE_JOB_NAME \
            --image=$INFER_IMAGE_URI \
            --region=$GOOGLE_REGION
    fi

    # -----------------------------------------------
    # Step 4: Configure Cloud Scheduler trigger
    # -----------------------------------------------
    echo "###########################################"
    echo "Configuring Cloud Scheduler inference trigger"
    echo "###########################################"

    CLOUD_RUN_JOB_URI="https://${GOOGLE_REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${GOOGLE_PROJECT}/jobs/${GOOGLE_INFERENCE_JOB_NAME}:run"

    gcloud scheduler jobs describe $GOOGLE_SCHEDULER_JOB_NAME --location=$GOOGLE_REGION > /dev/null 2>&1
    if [[ $? -ne 0 ]]; then
        echo "Creating Cloud Scheduler job '$GOOGLE_SCHEDULER_JOB_NAME' (every minute)..."
        gcloud scheduler jobs create http $GOOGLE_SCHEDULER_JOB_NAME \
            --location=$GOOGLE_REGION \
            --schedule="* * * * *" \
            --uri="$CLOUD_RUN_JOB_URI" \
            --http-method=POST \
            --oauth-service-account-email=$GOOGLE_SERVICE_ACCOUNT \
            --description="Triggers GNN inference Cloud Run Job every minute"
    else
        echo "Cloud Scheduler job '$GOOGLE_SCHEDULER_JOB_NAME' already exists - skipping."
    fi

    echo ""
    echo "GNN deployment complete:"
    echo "  Training pipeline: https://console.cloud.google.com/vertex-ai/pipelines?project=$GOOGLE_PROJECT"
    echo "  Cloud Run Job:     https://console.cloud.google.com/run/jobs?project=$GOOGLE_PROJECT"
    echo "  Cloud Scheduler:   https://console.cloud.google.com/cloudscheduler?project=$GOOGLE_PROJECT"
}
```

---

### 16.6 `Kill()` — Extended GNN cleanup

The existing `Kill()` function already cancels Vertex AI custom jobs and deletes endpoints/models (added during earlier development). Extend it to also delete the Cloud Run inference job and Cloud Scheduler trigger:

```bash
# Add to the Kill() function, after the existing "Cleaning up Vertex AI GNN resources..." block:

# Delete Cloud Scheduler inference trigger
echo "Deleting Cloud Scheduler inference trigger..."
gcloud scheduler jobs describe $GOOGLE_SCHEDULER_JOB_NAME --location=$GOOGLE_REGION > /dev/null 2>&1
if [[ $? -eq 0 ]]; then
    gcloud scheduler jobs delete $GOOGLE_SCHEDULER_JOB_NAME \
        --location=$GOOGLE_REGION --quiet
fi

# Delete Cloud Run inference Job
echo "Deleting Cloud Run inference job '$GOOGLE_INFERENCE_JOB_NAME'..."
gcloud run jobs describe $GOOGLE_INFERENCE_JOB_NAME --region=$GOOGLE_REGION > /dev/null 2>&1
if [[ $? -eq 0 ]]; then
    gcloud run jobs delete $GOOGLE_INFERENCE_JOB_NAME \
        --region=$GOOGLE_REGION --quiet
fi

# Cancel any running Vertex AI Pipeline jobs
echo "Cancelling any running Vertex AI Pipeline jobs..."
PIPELINE_JOBS=$(gcloud ai pipeline-jobs list \
    --region=$GOOGLE_REGION \
    --filter="displayName:gnn-training-pipeline AND state:PIPELINE_STATE_RUNNING" \
    --format="value(name)" 2>/dev/null || true)
if [ -n "$PIPELINE_JOBS" ]; then
    for pjob in $PIPELINE_JOBS; do
        echo "  Cancelling pipeline job: $pjob"
        gcloud ai pipeline-jobs cancel $pjob --region=$GOOGLE_REGION --quiet 2>/dev/null || true
    done
fi
```

---

### 16.7 `Delete()` — Clean up generated pipeline files

Add to the `rm -f` block in `Delete()`:

```bash
# Add to the existing rm -f block:
gnn/pipeline/submit_pipeline.py \   # generated from submit_pipeline.j2
gnn/pipeline/__pycache__/
```

---

### 16.8 `Help()` — Document new `--deploy gnn` behaviour

No structural change needed. Add a note to the existing help text clarifying that `--deploy gnn` now runs the full KFP pipeline instead of a direct training job:

```bash
# In the --deploy section description, update:
echo "  --deploy component1 component2"
echo "         (re)deploy specific components (valid components : spanner, operator, logcapture, git, gnn, metricscollector)"
echo "         Note: '--deploy gnn' submits the full Vertex AI KFP training pipeline,"
echo "         deploys the Cloud Run inference job, and configures Cloud Scheduler."
```

---

### 16.9 Summary of `install.sh` changes

| Function | Change type | What changes |
|---|---|---|
| `CheckGCPEnv()` | Addition | Warn if `kfp` Python package not installed |
| `SetDemoEnv()` | Addition | Export `GOOGLE_GNN_BUCKET`, `GOOGLE_PIPELINE_ROOT`, `GOOGLE_INFERENCE_JOB_NAME`, `GOOGLE_SCHEDULER_JOB_NAME` |
| `Create()` | Addition | Enable `cloudscheduler.googleapis.com` API; add `roles/aiplatform.user` + `roles/storage.objectAdmin` to Cloud Build SA; add `roles/aiplatform.modelUser` + `roles/cloudscheduler.admin` to `networkagent` SA; add Jinja templating for `submit_pipeline.py` |
| `Start()` | Addition | Create `pipeline-root/` and `snapshots/`/`models/` folder structure in GCS bucket |
| `DeployGNN()` | **Full replacement** | Build 3 images (train/serve/infer); submit KFP pipeline via `submit_pipeline.py`; deploy Cloud Run Job; configure Cloud Scheduler trigger |
| `Kill()` | Addition | Delete Cloud Scheduler trigger; delete Cloud Run inference job; cancel running pipeline jobs |
| `Delete()` | Addition | Remove generated `submit_pipeline.py` |
| `Help()` | Minor | Add note that `--deploy gnn` now uses KFP pipeline |

---

## 17. Open Questions

| Question | Owner | Notes |
|---|---|---|
| What GPU tier is appropriate for training? T4 is cheapest; A100 reduces training time for STGNN's RNN loop | Engineering | Start with T4, profile, upgrade if needed |
| Should we enable Vertex AI Vizier from day one or add it in Phase 6? | Engineering | Phase 6 is safer — get the pipeline working first |
| What are the val loss thresholds for the quality gate in `deploy_endpoint`? | Engineering | Run a baseline training run first to calibrate |
| Should the Cloud Scheduler inference interval stay at 60 seconds or can it be extended? | Product | 60s was the original background loop interval; may be fine to extend to 5 min given Cloud Run cold start overhead |
| Do we need a staging Vertex AI Endpoint before the production one? | Engineering | Recommended for the deploy step — deploy to staging, run smoke test, promote to prod |

---

## 18. Summary of Key Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Framework | PyTorch + PyG (unchanged) | Avoid high-risk rewrite; focus on operational layer |
| GNN library for future TF port | TF-GNN (documented as future phase) | Google-native, Vertex AI Endpoints support SavedModel natively |
| Pipeline orchestration | Vertex AI Pipelines (KFP v2) | Managed KFP on GCP, native Vertex AI integration |
| Training jobs | Vertex AI Custom Training (parallel) | All 3 models train simultaneously, each on dedicated GPU |
| Background inference | Cloud Run Job + Cloud Scheduler | Stateless, billed per-execution, no coupling to serving container |
| Online inference | Vertex AI Endpoint (custom container) | Autoscaling, managed health checks, IAP-protected |
| Model versioning | Vertex AI Model Registry | Full audit trail, rollback support, Vertex AI Experiments linkage |
| Data staging | GCS pickles (one ingest component, read by all training jobs) | Spanner queried once per pipeline run, not three times |
