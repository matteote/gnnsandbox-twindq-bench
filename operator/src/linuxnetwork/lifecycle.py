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
import kopf
import logging
import kubernetes
from linuxnetwork.lifecycle_tasks import (
    create_linux_network,
    delete_linux_network,
    get_detailed_network_status
)
from utils.compute import get_ip

logger = logging.getLogger(__name__)

#########################################################################
# DockerNetwork Lifecycle Management
#########################################################################

@kopf.on.create('google.dev', 'v1', 'linuxnetwork')
async def create_linuxnetwork(body, spec, name, namespace, uid, logger, **kwargs):
    """Handle LinuxNetwork creation using Ansible"""
    logger.info(f"Creating LinuxNetwork: {name} in namespace: {namespace}")
    logger.info(f"spec {spec}")

    ip_address = await get_ip("automation", "networkvm")
    if ip_address is None:
        raise kopf.TemporaryError("No ip address found on Network VM yet, temporary error - waiting", 10)
    logger.info(f"network vm address = {ip_address}")

    try:
        # Update status to indicate creation has started
        await update_status(name, namespace, "Creating", "Creating Linux network")

        # Create the Linux network using Ansible
        result = await create_linux_network(ip_address, spec)

        if result['success']:
            # if network_type == 'management' also add the default_interface to status
            if spec.get('network_type') == 'management':
                logger.info("Linux network is of type 'management', performing additional setup")

                # Extract default interface if available
                default_interface = result.get('default_interface', 'unknown')
                interface_ip = result.get('interface_ip', 'unknown')
                default_gateway = result.get('default_gateway', 'unknown')
                extra_status = {'interface': default_interface, 'gateway': default_gateway, 'interface_ip': interface_ip}
            else:
                extra_status = {}
            
            # Update status to ready with full details
            await update_status(
                name, namespace, "Ready", 
                f"Linux network {spec['name']} created successfully",
                extra_status=extra_status
            )
            logger.info(f"Successfully created LinuxNetwork {name}")
        else:
            await update_status(name, namespace, "Failed", f"Failed to create network: {result['error']}")
            raise kopf.PermanentError(f"Linux network creation failed: {result['error']}")

    except (kopf.TemporaryError, kopf.PermanentError):
        raise
    except Exception as e:
        logger.error(f"Failed to create LinuxNetwork {name}: {e}")
        await update_status(name, namespace, "Failed", str(e))
        raise

@kopf.on.delete('google.dev', 'v1', 'linuxnetwork')
async def delete_linuxnetwork(body, spec, status, name, namespace, logger, **kwargs):
    """Handle LinuxNetwork deletion using Ansible"""
    logger.info(f"Deleting LinuxNetwork: {name} in namespace: {namespace}")

    ip_address = await get_ip("automation", "networkvm")
    if ip_address is None:
        raise kopf.TemporaryError("No ip address found on Network VM yet, temporary error - waiting", 10)
    logger.info(f"network vm address = {ip_address}")

    logger.info("TODO::make sure all dependent resources are deleted first")

    try:        
        # Delete the Docker network using Ansible
        result = await delete_linux_network(ip_address, spec, status)
        
        if result['success']:
            logger.info(f"Successfully deleted DockerNetwork {spec.get('network_name')}")
        else:
            logger.warning(f"Failed to delete Docker network {spec.get('network_name')}: {result['error']}")
            # Don't raise error on delete failure - resource should still be removed from Kubernetes
            
    except Exception as e:
        logger.error(f"Error during DockerNetwork deletion {name}: {e}")
        # Don't raise error on delete failure

@kopf.timer('google.dev', 'v1', 'linuxnetwork', interval=300.0)
async def monitor_linuxnetwork(body, spec, name, namespace, uid, logger, **kwargs):
    """Monitor LinuxNetwork status with detailed state tracking"""

    # Do not monitor until after first successful install
    status_dict = body.get('status', {})
    if not status_dict or status_dict.get('phase') in ["Pending", "Creating", None]:
        logger.debug(f"Skipping monitor for {name}, network not fully created yet")
        return

    ip_address = await get_ip("automation", "networkvm")
    if ip_address is None:
        logger.warning("No ip address found on Network VM yet, skipping monitoring check")
        return

    try:
        network_name = spec.get('name', name)
        
        # Get detailed status including bridge and veth state
        status = await get_detailed_network_status(ip_address, network_name)
        
        # Get previous state
        previous_phase = status_dict.get('phase')
        previous_exists = previous_phase == "Ready"
        previous_state = status_dict.get('operational_state', 'unknown')
        
        current_exists = status['exists']
        current_state = status.get('operational_state', 'unknown')
        
        state_changed = False
        
        # Check existence change
        if not current_exists and previous_exists:
            state_changed = True
            logger.warning(f"LinuxNetwork {name} state changed: Ready -> Failed (deleted)")
            await update_status(name, namespace, "Failed", "Linux network no longer exists")
        
        elif current_exists and not previous_exists:
            state_changed = True
            logger.info(f"LinuxNetwork {name} state changed: Failed -> Ready (restored)")
            await update_status(name, namespace, "Ready", "Linux network is available",
                              extra_status={'operational_state': current_state})
        
        # Check operational state change
        elif current_exists and previous_exists and current_state != previous_state:
            state_changed = True
            logger.info(f"LinuxNetwork {name} operational state changed: {previous_state} -> {current_state}")
            await update_status(name, namespace, "Ready", f"Bridge state: {current_state}",
                              extra_status={'operational_state': current_state})
        
        if not state_changed:
            logger.debug(f"LinuxNetwork {name} state unchanged, skipping K8s status update")
        
        # Sync to Spanner - sync function has its own state change detection
        # Only writes to Spanner if bridge/veth state has changed (SCD Type 2)
        from graph.lifecycle_tasks import sync_host_network_bridge
        await sync_host_network_bridge(body, spec, name, namespace, status, logger)
            
    except Exception as e:
        logger.error(f"Failed to check network status for {name}: {e}")

#########################################################################
# Status Management
#########################################################################

async def update_status(name: str, namespace: str, phase: str, message: str, extra_status: dict = None):
    """Update the status of a LinuxNetwork resource"""
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='LinuxNetwork')

    resource = api.get(name=name, namespace=namespace)
    resource_dict = resource.to_dict()

    if 'status' not in resource_dict:
        resource_dict['status'] = {}

    status = {
        'phase': phase,
        'message': message
    }
    
    # Add any additional status fields
    if extra_status:
        status.update(extra_status)

    logger.debug(f"Updating status for LinuxNetwork {name}: {status}")

    resource_dict['status'].update(status)

    try:
        api.patch(
            namespace=namespace,
            name=name,
            body=resource_dict,
            content_type='application/merge-patch+json',
            subresource='status'
        )
    except kubernetes.client.rest.ApiException as e:
        if e.status == 422 and "status" in str(e):
            logger.warning(f"Status subresource not enabled for LinuxNetwork {name}, skipping status update.")
        else:
            logger.error(f"Failed to update status for LinuxNetwork {name}: {e}")
