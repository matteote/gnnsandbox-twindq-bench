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

from typing import Annotated, Dict
import logging
import kubernetes
from utils.k8s import get_client
import json
import utils.git_helpers as git
import yaml
import utils.globals as globals
from mcp.types import ToolAnnotations


logger = logging.getLogger(__name__)

# if GITOPS true then the service deletion / creation
# is performed through the Gitea repository + Config Sync
# Otherwise it is executed directly through K8s apply/delete
GITOPS = False


######################################################################
# Get TrafficTest CRD Definition
######################################################################
@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def getTrafficTestDefinition()-> str:
    """
    Fetch the TrafficTest Custom Resource Definition (CRD) specification.
    
    This provides the complete schema and documentation for creating TrafficTest resources,
    including all supported traffic patterns, configuration options, and field descriptions.

    Returns:
        The TrafficTest CRD as a JSON object containing the OpenAPI schema and documentation
    """
    logger.info("Getting TrafficTest CRD definition")

    client = kubernetes.dynamic.DynamicClient(get_client())

    try:
        network_api = client.resources.get(
            api_version="apiextensions.k8s.io/v1", 
            kind="CustomResourceDefinition",
        )
        
        # Get the TrafficTest CRD
        try:
            crd = network_api.get(name="traffictests.google.dev")
            crd_dict = crd.to_dict()
            text_representation = json.dumps(crd_dict, indent=2)
            logger.debug(f"Found TrafficTest CRD: {text_representation}")
            return text_representation
        except kubernetes.client.rest.ApiException as get_e:
            if get_e.status == 404:
                return "TrafficTest CRD not found. It may not be installed in the cluster."
            else:
                raise get_e
        
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            return "Unable to access CRD definitions"
        else:
            logger.error(f"Error retrieving TrafficTest CRD: {e}")
            return f"Error retrieving TrafficTest CRD: {e}"

######################################################################
# Get existing traffic tests
######################################################################
@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def getRunningTests()-> str:
    """
    Fetch the traffic test instances that are currently running.

    Returns:
        A list of traffic tests and their status as JSON objects
    """
    logger.info("get running traffic tests")

    client = kubernetes.dynamic.DynamicClient(get_client())
    try:
        test_list=""
        network_api = client.resources.get(
            api_version="google.dev/v1", 
            kind="TrafficTest",
            namespace="default"
        )
        tests=network_api.get()
        for t in tests.items:
            logger.debug(t)
            test_dict = t.to_dict()
            text_representation = json.dumps(test_dict, indent=2)
            test_list = test_list + text_representation + "\n"
        
        logger.debug(test_list)
        return test_list if test_list else "No traffic tests found"

    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            return """No traffic tests found"""
        else:
            logger.error(e)
            return f"Error retrieving traffic tests: {e}"

######################################################################
# Run new traffic test
######################################################################
@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
def runTest(
    name: Annotated[str, "The name of the traffic test to create"], 
    spec: Annotated[Dict, "The kubernetes spec object for the TrafficTest. Must include required fields: source_devices (list), destination_device (string), protocol (TCP or UDP). Optional fields: duration, bandwidth, pattern_type, pattern_config, port, concurrent_users, session_duration, think_time, metrics_enabled, metrics_interval"]
    )-> str:
    """
    Tool used to deploy (also called instantiate) a new traffic test between network devices.
    
    The traffic test supports various traffic patterns (constant, periodic, burst, poisson) and comprehensive
    configuration options for realistic network testing scenarios.
    
    Only call this tool if explicitly stated in the network agent query or question.
    Always ask for an explicit confirmation by yes or no before creating the test.
    
    The spec must be the complete TrafficTest specification containing all required and optional fields.
    You can use getTrafficTestDefinition() to see the complete schema.
    
    Returns:
        Status message indicating success or failure
    """
    logger.info("run a new traffic test %s with spec: %s", name, spec)

    # If the user gave the entire spec block then only keep its content
    if "spec" in spec.keys():
        spec = spec["spec"]

    test_manifest = { 
        "apiVersion": "google.dev/v1",
        "kind": "TrafficTest",
        "metadata": {
            "name": name,
            "namespace": "default",
        },
        "spec": spec
    }

    test_manifest_yaml = yaml.dump(test_manifest, indent=2, allow_unicode=True, default_flow_style=False)
    logger.info(f"Creating traffic test:\n{test_manifest_yaml}")
    
    if GITOPS:
        filename = f"traffictest-{name}.yaml"
        result = git.commit_git_file(filename,
                                 f"Deployment of TrafficTest {name}",
                                 test_manifest_yaml)
        if result:
            return f"Traffic test {name} successfully submitted for deployment"
        else:
            return f"Traffic test {name} could not be deployed:\n```yaml\n{test_manifest_yaml}\n```"
    else:
        client = kubernetes.dynamic.DynamicClient(get_client())
        try:
            network_api = client.resources.get(
                api_version="google.dev/v1", 
                kind="TrafficTest",
            )
            network_api.create(test_manifest)
            return f"Traffic test {name} started successfully"

        except kubernetes.client.rest.ApiException as e:
            logger.info(e.status)
            if e.status == 409:
                return f"Traffic test {name} already exists"
            else:
                logger.error(e)
                return f"Error creating traffic test: {e}"

######################################################################
# Delete running traffic test
######################################################################
@globals.networkagent_mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
def deleteTest(
    name: Annotated[str, "The name of the traffic test to delete"], 
    )-> str:
    """
    Delete a running traffic test.
    Only call this tool if explicitly stated in the network agent query or question.
    """
    logger.info("delete traffic test %s", name)

    if GITOPS:
        filename = f"traffictest-{name}.yaml"
        result = git.delete_git_file(filename, f"TrafficTest {name} deletion")
        if result:
            return f"Traffic test {name} successfully submitted for deletion"
        else:
            return f"Traffic test {name} could not be deleted"
    else:
        client = kubernetes.dynamic.DynamicClient(get_client())
        try:
            network_api = client.resources.get(
                api_version="google.dev/v1", 
                kind="TrafficTest",
            )
            network_api.delete(name=name, namespace="default")
            return f"Traffic test {name} deleted successfully"

        except kubernetes.client.rest.ApiException as e:
            logger.error(e)
            if e.status == 404:
                return f"Traffic test {name} not found"
            else:
                return f"Error deleting traffic test: {e}"
