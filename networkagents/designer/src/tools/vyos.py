# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import kubernetes
from kubernetes.client.rest import ApiException
from utils.k8s import get_client as get_k8s_client

SPANNER_INSTANCE = 'networktopology-instance'
SPANNER_DATABASE = 'networktopology-db'
GRAPH_NAME = 'networkGraph'

logger = logging.getLogger(__name__)

def getVyosDescriptors() -> list[dict]:
    """
    Retrieve the Vyos infrastructure, underlay and vpn k8s custom resource descriptors.

    These descriptors provide the automation available to deploy a complete Vyos L3VPN

    Args:
        None

    Returns:
        list[dict]: An array of Kubernetes CustomResourceDefinition (CRD) objects
    """
    logger.info(f"Fetching VyOS descriptors")

    try:
        client = kubernetes.dynamic.DynamicClient(get_k8s_client())
        network_api = client.resources.get(
            api_version="apiextensions.k8s.io/v1", 
            kind="CustomResourceDefinition",
        )
        items=network_api.get(label_selector="type=network,agent-accessible=true")
        logger.debug("NW ITEMS")
        logger.debug(items)
        services=[]
        for item in items.items:
            services.append(item.to_dict())

        return services

    except Exception as e:
        logger.error(e)


def getDeployedCRs() -> list[dict]:
    """
    Retrieve all deployed Vyos CRs

    Returns:
        list[dict]: list of network infrastructure, underlay, l3vpns, and test instances. 
    """
    logger.info(f"Fetching VyOS resources")

    instances = []
    client = kubernetes.dynamic.DynamicClient(get_k8s_client())

    for kind in ["VyOSInfrastructure", "VyOSUnderlay", "VyOSL3VPN", "TrafficTest"]:
        try:
            api = client.resources.get(api_version="google.dev/v1", kind=kind)
            resources = api.get(namespace='default')
            for item in resources.items:
                instances.append(item.to_dict())
        except Exception as e:
            logger.error(f"Error fetching {kind} resources: {e}", exc_info=True)

    return instances


def deploySpec(descriptor: dict) -> str:
    """
    Deploy a VyOS Custom Resource descriptor to the cluster.
    If the resource already exists, it will be updated (merge-patch).

    Args:
        descriptor (dict): The Kubernetes resource descriptor (YAML/dict)

    Returns:
        str: Success or error message
    """
    logger.info(f"Deploying descriptor: {descriptor.get('kind')}/{descriptor.get('metadata', {}).get('name')}")
    try:
        client = kubernetes.dynamic.DynamicClient(get_k8s_client())
        kind = descriptor.get("kind")
        name = descriptor.get("metadata", {}).get("name")
        namespace = descriptor.get("metadata", {}).get("namespace", "default")
        
        resource_api = client.resources.get(
            api_version="google.dev/v1", 
            kind=kind
        )

        try:
            resource_api.create(descriptor, namespace=namespace)
            return f"Successfully created {kind}/{name}"
        except ApiException as exc:
            if exc.status == 409:
                # Already exists — merge-patch
                resource_api.patch(
                    body=descriptor,
                    name=name,
                    namespace=namespace,
                    content_type='application/merge-patch+json'
                )
                return f"Successfully updated {kind}/{name}"
            else:
                raise exc

    except Exception as e:
        logger.error(f"Failed to deploy descriptor: {e}")
        return f"Error deploying {descriptor.get('kind', 'Unknown')}: {str(e)}"


def deleteSpec(kind: str, name: str, namespace: str = "default") -> str:
    """
    Delete a VyOS Custom Resource from the cluster.

    Args:
        kind (str): The Kind of the resource (e.g., VyOSL3VPN)
        name (str): The name of the resource
        namespace (str): The namespace

    Returns:
        str: Success or error message
    """
    logger.info(f"Deleting resource: {kind}/{name}")
    try:
        client = kubernetes.dynamic.DynamicClient(get_k8s_client())
        resource_api = client.resources.get(
            api_version="google.dev/v1", 
            kind=kind
        )
        resource_api.delete(name=name, namespace=namespace)
        return f"Successfully deleted {kind}/{name}"
    except ApiException as e:
        if e.status == 404:
            return f"Resource {kind}/{name} not found, already deleted."
        logger.error(f"Failed to delete resource: {e}")
        return f"Error deleting {kind}/{name}: {str(e)}"
    except Exception as e:
        logger.error(f"Failed to delete resource: {e}")
        return f"Error deleting {kind}/{name}: {str(e)}"


def getDeploymentStatus(kind: str, name: str, namespace: str = "default") -> dict:
    """
    Retrieve the current status of a deployed VyOS resource.

    Args:
        kind (str): The Kind of the resource
        name (str): The name of the resource
        namespace (str): The namespace

    Returns:
        dict: The resource status block (phase, message, etc.)
    """
    try:
        client = kubernetes.dynamic.DynamicClient(get_k8s_client())
        resource_api = client.resources.get(
            api_version="google.dev/v1", 
            kind=kind
        )
        resource = resource_api.get(name=name, namespace=namespace)
        return resource.status.to_dict() if hasattr(resource, 'status') else {"phase": "Unknown"}
    except Exception as e:
        logger.error(f"Error fetching status for {kind}/{name}: {e}")
        return {"phase": "Error", "message": str(e)}

