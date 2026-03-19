# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""KFP component: Evaluate a trained GNN model on the held-out validation set."""

from kfp.dsl import component, Input, Output, Artifact, Metrics


@component(
    base_image="python:3.12-slim",
    packages_to_install=[
        "google-cloud-aiplatform==1.49.0",
    ],
)
def evaluate_model(
    project: str,
    region: str,
    model_name: str,
    model_gcs_path: Input[Artifact],
    snapshots_gcs_path: Input[Artifact],
    scalers_gcs_path: Input[Artifact],
    train_image_uri: str,
    service_account: str,
    metrics: Output[Metrics],
) -> float:
    """Evaluates a trained GNN model using a Vertex AI Custom Job.

    Runs the evaluation script in the training container on the last 20% of
    snapshots (held-out validation set).  Results are written to the KFP
    Metrics artifact for display in the Vertex AI Pipelines UI.

    Args:
        model_name: One of 'dgat', 'hetgnn', or 'stgnn'.

    Returns:
        val_loss (float) — primary metric used by the quality-gate condition.
    """
    import json
    import logging
    import tempfile
    import os

    from google.cloud import aiplatform, storage

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(f"evaluate_{model_name}")

    aiplatform.init(project=project, location=region)

    eval_output_path = f"{model_gcs_path.uri}/eval"

    worker_pool_specs = [
        {
            "machine_spec": {
                "machine_type": "n1-standard-4",
                "accelerator_type": "NVIDIA_TESLA_T4",
                "accelerator_count": 1,
            },
            "replica_count": 1,
            "container_spec": {
                "image_uri": train_image_uri,
                "command": ["python", "evaluate.py"],
                "env": [
                    {"name": "MODEL_NAME", "value": model_name},
                    {"name": "MODEL_GCS_PATH", "value": model_gcs_path.uri},
                    {"name": "SNAPSHOTS_GCS_PATH", "value": snapshots_gcs_path.uri},
                    {"name": "SCALERS_GCS_PATH", "value": scalers_gcs_path.uri},
                    {"name": "EVAL_OUTPUT_PATH", "value": eval_output_path},
                    # Evaluate on last 20% of snapshots
                    {"name": "EVAL_SPLIT", "value": "0.2"},
                ],
            },
        }
    ]

    job = aiplatform.CustomJob(
        display_name=f"gnn-eval-{model_name}",
        worker_pool_specs=worker_pool_specs,
        base_output_dir=eval_output_path,
        project=project,
        location=region,
    )

    logger.info(f"Submitting evaluation job for {model_name}...")
    job.run(
        service_account=service_account if service_account else None,
        sync=True,
    )

    # Read eval_metrics.json written by evaluate.py
    storage_client = storage.Client()
    bucket_name = eval_output_path.removeprefix("gs://").split("/")[0]
    prefix = "/".join(eval_output_path.replace("gs://", "").split("/")[1:])
    bucket = storage_client.bucket(bucket_name)

    val_loss = 9999.0
    try:
        metrics_blob = bucket.blob(f"{prefix}/eval_metrics.json")
        eval_data = json.loads(metrics_blob.download_as_text())
        val_loss = float(eval_data.get("val_loss", 9999.0))

        # Log all metrics to KFP Metrics artifact
        for key, value in eval_data.items():
            if isinstance(value, (int, float)):
                metrics.log_metric(f"{model_name}/{key}", value)
    except Exception as e:
        logger.warning(f"Could not read eval_metrics.json: {e}. Using default val_loss=9999")

    logger.info(f"Evaluation complete for {model_name}: val_loss={val_loss}")
    metrics.log_metric(f"{model_name}/val_loss", val_loss)

    return val_loss
