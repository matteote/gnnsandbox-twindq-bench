# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""KFP component: Register the trained HetGNN model to Vertex AI Model Registry."""

from kfp.dsl import component, Input, Output, Artifact


@component(
    base_image="python:3.12-slim",
    packages_to_install=[
        "google-cloud-aiplatform==1.49.0",
        "google-cloud-storage==2.16.0",
    ],
)
def register_model(
    project: str,
    region: str,
    serve_image_uri: str,
    hetgnn_model_gcs_path: Input[Artifact],
    hetgnn_val_loss: float,
    gcs_bucket: str,
    run_id: str,
    hetgnn_model_resource_name: Output[Artifact],
):
    """Uploads the trained HetGNN model to Vertex AI Model Registry.

    Attaches metadata (val loss, run ID, timestamp) to the model version.
    Also writes the latest_run.json manifest to GCS so the Cloud Run
    inference job can find the current best model artefacts.
    """
    import json
    import logging
    from datetime import datetime

    from google.cloud import aiplatform, storage

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("register_model")

    aiplatform.init(project=project, location=region)

    logger.info(f"Registering hetgnn model from {hetgnn_model_gcs_path.uri}...")

    model = aiplatform.Model.upload(
        display_name="gnn-hetgnn",
        artifact_uri=hetgnn_model_gcs_path.uri,
        serving_container_image_uri=serve_image_uri,
        serving_container_predict_route="/predict",
        serving_container_health_route="/health",
        serving_container_ports=[{"containerPort": 8080}],
        labels={
            "model_type": "hetgnn",
            "run_id": run_id[:63],  # label value max 63 chars
        },
        description=(
            f"GNN HetGNN model — run {run_id[:8]} — "
            f"val_loss={hetgnn_val_loss:.4f}"
        ),
    )

    resource_name = model.resource_name
    hetgnn_model_resource_name.uri = resource_name
    logger.info(f"Registered hetgnn: {resource_name}")

    # Write latest_run.json manifest to GCS for the Cloud Run inference job
    manifest = {
        "run_id": run_id,
        "registered_at": datetime.utcnow().isoformat(),
        "models": {
            "hetgnn": {
                "resource_name": resource_name,
                "gcs_path": hetgnn_model_gcs_path.uri,
                "val_loss": hetgnn_val_loss,
            }
        },
    }
    storage_client = storage.Client()
    bucket = storage_client.bucket(gcs_bucket)
    bucket.blob("models/latest/latest_run.json").upload_from_string(
        json.dumps(manifest, indent=2),
        content_type="application/json",
    )
    logger.info(f"Wrote latest_run.json to gs://{gcs_bucket}/models/latest/latest_run.json")
