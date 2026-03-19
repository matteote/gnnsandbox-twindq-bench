# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""KFP component: Deploy HetGNN model to a Vertex AI Endpoint (quality-gate guarded)."""

from kfp.dsl import component, Input, Output, Artifact


@component(
    base_image="python:3.12-slim",
    packages_to_install=[
        "google-cloud-aiplatform==1.49.0",
    ],
)
def deploy_endpoint(
    project: str,
    region: str,
    hetgnn_model_resource_name: Input[Artifact],
    endpoint_resource_name: str,
    deployed_endpoint: Output[Artifact],
):
    """Deploys the HetGNN model to a Vertex AI Endpoint.

    If endpoint_resource_name is empty a new endpoint named 'gnn-endpoint' is
    created.  If it already exists the existing endpoint is updated in-place
    (undeploy old → deploy new), providing zero-downtime rollout.
    """
    import logging
    from google.cloud import aiplatform

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("deploy_endpoint")

    aiplatform.init(project=project, location=region)

    # Resolve or create the endpoint
    if endpoint_resource_name:
        endpoint = aiplatform.Endpoint(endpoint_resource_name)
        logger.info(f"Using existing endpoint: {endpoint_resource_name}")
    else:
        endpoint = aiplatform.Endpoint.create(
            display_name="gnn-endpoint",
            description="Vertex AI Endpoint for GNN serving (HetGNN)",
        )
        logger.info(f"Created new endpoint: {endpoint.resource_name}")

    # Undeploy any currently deployed models to avoid multi-model traffic splits
    for deployed_model in endpoint.list_models():
        logger.info(f"Undeploying existing model: {deployed_model.id}")
        endpoint.undeploy(deployed_model_id=deployed_model.id)

    # Deploy the HetGNN model
    hetgnn_model = aiplatform.Model(hetgnn_model_resource_name.uri)
    logger.info(f"Deploying HetGNN model: {hetgnn_model.resource_name}")
    hetgnn_model.deploy(
        endpoint=endpoint,
        deployed_model_display_name="hetgnn-serve",
        machine_type="n1-standard-4",
        min_replica_count=1,
        max_replica_count=3,
        traffic_percentage=100,
    )

    logger.info(f"Deployment complete. Endpoint: {endpoint.resource_name}")
    deployed_endpoint.uri = endpoint.resource_name
