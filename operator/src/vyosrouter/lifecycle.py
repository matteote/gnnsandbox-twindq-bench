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
from typing import Dict, Any, Optional
import kubernetes
from vyosrouter.lifecycle_tasks import (
    create_vyos_router,
    delete_vyos_router,
    update_vyos_router,
    configure_vyos_router,
    check_linux_networks_ready
)
from utils.compute import *
from graph.lifecycle_tasks import sync_physical_router

logger = logging.getLogger(__name__)

#########################################################################
# VyOSRouter Lifecycle Management
#########################################################################

@kopf.on.create('google.dev', 'v1', 'vyosrouter')
async def create_vyosrouter(body, spec, name, namespace, uid, logger, **kwargs):
    """Handle VyOSRouter creation using Ansible"""
    logger.info(f"Creating VyOSRouter: {name} in namespace: {namespace}")

    ip_address = await get_ip("automation", "networkvm")
    if ip_address is None:
        raise kopf.TemporaryError("No ip address found on Network VM yet, temporary error - waiting", 10)
    logger.info(f"network vm address = {ip_address}")

    # Update status to indicate pending (automatically syncs to Spanner)
    await update_status(name, namespace, "Pending", "Validating and waiting for dependencies",
                       body=body, spec=spec, uid=uid, logger_obj=logger)

    # Extract router configuration from spec
    router_config = {
        'name': name,
        'hostname': spec['hostname'],
        'router_id': spec['router_id'],
        'image': spec.get('image', 'vyos:1.5'),
        'source_network': spec.get('source_network'),
        'interfaces': spec.get('interfaces', []),
        'vrfs': spec.get('vrfs', []),
        'protocols': spec.get('protocols', {}),
        'services': spec.get('services', {}),
        'qos': spec.get('qos', {}),
        'firewall': spec.get('firewall', {}),
        'traffic_policy': spec.get('traffic_policy', {})
    }

    logger.info(f"VyOSRouter config: {router_config}")

    # Check if all required LinuxNetwork CRs are ready
    interfaces = router_config.get('interfaces', [])
    if interfaces:
        all_ready, not_ready_networks = await check_linux_networks_ready(interfaces, namespace)
        
        if not all_ready:
            # Build detailed error message
            network_details = []
            for net in not_ready_networks:
                network_details.append(f"{net['name']} (phase: {net['phase']}, message: {net['message']})")
            
            error_msg = f"Waiting for LinuxNetwork(s) to be ready: {', '.join(network_details)}"
            logger.warning(f"VyOSRouter {name}: {error_msg}")
            
            await update_status(name, namespace, "Pending", error_msg)
            
            # Raise temporary error to retry later
            raise kopf.TemporaryError(error_msg, delay=20)
        
    try:
        # Update status to indicate creation has started (automatically syncs to Spanner)
        await update_status(name, namespace, "Creating", "Creating VyOS router container",
                           body=body, spec=spec, uid=uid, logger_obj=logger)
        
        # Create the VyOS router container using Ansible
        result = await create_vyos_router(ip_address, router_config)
        
        if result['success']:
            # Update status to configuring (automatically syncs to Spanner)
            await update_status(
                name, namespace, "Configuring", 
                "Applying VyOS configuration",
                body=body, spec=spec, uid=uid, logger_obj=logger,
                container_id=result.get('container_id')
            )
            
            # Configure the VyOS router
            config_result = await configure_vyos_router(ip_address, router_config)
            
            if config_result['success']:
                # Initialize interface status from spec
                interfaces_status = []
                for iface in router_config['interfaces']:
                    interfaces_status.append({
                        'name': iface['name'],
                        'status': 'up',  # Assume up initially
                        'linux_network': iface.get('linux_network', '')
                    })
                
                # Update status to running with interface details (automatically syncs to Spanner)
                await update_status(
                    name, namespace, "Running", 
                    f"VyOS router {name} is running and configured",
                    body=body, spec=spec, uid=uid, logger_obj=logger,
                    container_id=result.get('container_id'),
                    ip_address=result.get('management_ip'),
                    interfaces=interfaces_status,
                    applied_config=config_result.get('applied_config')
                )
                logger.info(f"Successfully created and configured VyOSRouter {name}")
            else:
                # Configuration errors - check if transient or permanent
                error_msg = config_result.get('error', 'Unknown configuration error')
                if _is_transient_error(error_msg):
                    logger.warning(f"Transient configuration error for {name}, will retry: {error_msg}")
                    await update_status(name, namespace, "Pending", f"Configuration pending retry: {error_msg}",
                                       body=body, spec=spec, uid=uid, logger_obj=logger)
                    raise kopf.TemporaryError(f"Transient configuration error: {error_msg}", delay=30)
                else:
                    await update_status(name, namespace, "Failed", f"Configuration failed: {error_msg}",
                                       body=body, spec=spec, uid=uid, logger_obj=logger)
                    raise kopf.PermanentError(f"VyOS router configuration failed: {error_msg}")
        else:
            # Container creation failed - check if transient or permanent
            error_msg = result.get('error', 'Unknown creation error')
            if _is_transient_error(error_msg):
                logger.warning(f"Transient creation error for {name}, will retry: {error_msg}")
                await update_status(name, namespace, "Pending", f"Creation pending retry: {error_msg}",
                                   body=body, spec=spec, uid=uid, logger_obj=logger)
                raise kopf.TemporaryError(f"Transient creation error: {error_msg}", delay=30)
            else:
                await update_status(name, namespace, "Failed", f"Failed to create container: {error_msg}",
                                   body=body, spec=spec, uid=uid, logger_obj=logger)
                raise kopf.PermanentError(f"VyOS router creation failed: {error_msg}")

    except (kopf.TemporaryError, kopf.PermanentError):
        # Re-raise kopf control exceptions without double-handling
        raise
    except Exception as e:
        # Unexpected exception - check if it looks transient before permanently failing
        error_msg = str(e)
        logger.error(f"Unexpected error creating VyOSRouter {name}: {error_msg}")
        if _is_transient_error(error_msg):
            logger.warning(f"Treating as transient error, will retry: {error_msg}")
            await update_status(name, namespace, "Pending", f"Retrying after error: {error_msg}",
                               body=body, spec=spec, uid=uid, logger_obj=logger)
            raise kopf.TemporaryError(error_msg, delay=30)
        else:
            await update_status(name, namespace, "Failed", error_msg,
                               body=body, spec=spec, uid=uid, logger_obj=logger)
            raise

@kopf.on.update('google.dev', 'v1', 'vyosrouter', field='spec')
async def update_vyosrouter(body, spec, name, namespace, uid, logger, **kwargs):
    """Handle VyOSRouter updates using Ansible"""
    logger.info(f"Updating VyOSRouter: {name} in namespace: {namespace}")

    ip_address = await get_ip("automation", "networkvm")
    if ip_address is None:
        raise kopf.TemporaryError("No ip address found on Network VM yet, temporary error - waiting", 10)
    logger.info(f"network vm address = {ip_address}")

    try:
        await update_status(name, namespace, "Updating", "Updating VyOS router configuration",
                           body=body, spec=spec, uid=uid, logger_obj=logger)
        
        # Extract updated router configuration
        router_config = {
            'name': name,
            'hostname': spec['hostname'],
            'router_id': spec['router_id'],
            'image': spec.get('image', 'vyos:1.5'),
            'interfaces': spec.get('interfaces', []),
            'vrfs': spec.get('vrfs', []),
            'protocols': spec.get('protocols', {}),
            'services': spec.get('services', {}),
            'qos': spec.get('qos', {}),
            'firewall': spec.get('firewall', {}),
            'traffic_policy': spec.get('traffic_policy', {})
        }
        
        # Update the VyOS router using Ansible
        result = await update_vyos_router(ip_address, router_config)
        
        if result['success']:
            await update_status(name, namespace, "Running", "VyOS router updated successfully",
                               body=body, spec=spec, uid=uid, logger_obj=logger,
                               applied_config=result.get('applied_config'))
            logger.info(f"Successfully updated VyOSRouter {name}")
        else:
            await update_status(name, namespace, "Failed", f"Failed to update router: {result['error']}",
                               body=body, spec=spec, uid=uid, logger_obj=logger)
            raise kopf.PermanentError(f"VyOS router update failed: {result['error']}")
            
    except Exception as e:
        logger.error(f"Failed to update VyOSRouter {name}: {e}")
        await update_status(name, namespace, "Failed", str(e),
                           body=body, spec=spec, uid=uid, logger_obj=logger)
        raise

@kopf.on.delete('google.dev', 'v1', 'vyosrouter')
async def delete_vyosrouter(body, spec, name, namespace, logger, **kwargs):
    """Handle VyOSRouter deletion using Ansible"""
    logger.info(f"Deleting VyOSRouter: {name} in namespace: {namespace}")

    ip_address = await get_ip("automation", "networkvm")
    if ip_address is None:
        raise kopf.TemporaryError("No ip address found on Network VM yet, temporary error - waiting", 10)
    logger.info(f"network vm address = {ip_address}")
    
    try:
        # Extract interfaces from spec for proper veth cleanup
        interfaces = spec.get('interfaces', [])
        
        # Delete the VyOS router container using Ansible
        result = await delete_vyos_router(ip_address, name, interfaces)
        
        if result['success']:
            logger.info(f"Successfully deleted VyOSRouter {name}")
        else:
            logger.warning(f"Failed to delete VyOS router {name}: {result['error']}")
            # Don't raise error on delete failure - resource should still be removed from Kubernetes
            
    except Exception as e:
        logger.error(f"Error during VyOSRouter deletion {name}: {e}")
        # Don't raise error on delete failure

@kopf.timer('google.dev', 'v1', 'vyosrouter', interval=120.0)
async def monitor_vyosrouter(body, spec, name, namespace, logger, **kwargs):
    """Monitor VyOSRouter status periodically and report only on state changes"""

    # Do not monitor until after first successful install
    status_dict = body.get('status', {})
    if not status_dict or status_dict.get('phase') in ["Pending", "Creating", "Configuring", "Updating", None]:
        logger.debug(f"Skipping monitor for {name}, router not fully configured yet")
        return

    ip_address = await get_ip("automation", "networkvm")
    if ip_address is None:
        logger.warning(f"No ip address found on Network VM yet, skipping monitoring check for {name}")
        return

    try:
        from vyosrouter.lifecycle_tasks import get_vyos_router_status
        result = await get_vyos_router_status(ip_address, name)
        
        if not result.get('success', False):
            logger.warning(f"Failed to get router status for {name}: {result.get('error')}")
            return
            
        running = result.get('running', False)
        
        # Get previous state from status
        previous_phase = status_dict.get('phase')
        previous_running = previous_phase == "Running"
        
        if not running:
            # Container is down - only update if state changed
            if previous_running:
                logger.warning(f"VyOSRouter {name} state changed: Running -> Failed")
                await update_status(name, namespace, "Failed", "VyOS router container is not running",
                                   body=body, spec=spec, uid=kwargs.get('uid'), logger_obj=logger)
            return

        # Container is running, extract protocols/interface status
        protocols = result.get('protocols', {})
        ospf_status = result.get('ospf_status', {})
        bgp_status = result.get('bgp_status', {})
        mpls_status = result.get('mpls_status', {})
        interface_status = result.get('interface_status', [])

        # Update protocols dict
        if ospf_status:
            protocols['ospf'] = ospf_status
        if bgp_status:
            protocols['bgp'] = bgp_status
        if mpls_status:
            protocols['mpls'] = mpls_status

        # If we have interface status, we can update it
        status_interfaces = []
        if interface_status:
           status_interfaces = interface_status

        # Compare with previous state to detect changes
        previous_interfaces = status_dict.get('interfaces', [])
        
        # Check if state has changed
        state_changed = False
        
        # Check if phase changed from Failed back to Running
        if not previous_running:
            state_changed = True
            logger.info(f"VyOSRouter {name} state changed: Failed -> Running")
        
        # Check if interfaces changed
        if _interfaces_changed(previous_interfaces, status_interfaces):
            state_changed = True
            logger.info(f"VyOSRouter {name} interface state changed")
        
        # Only update status if something changed
        if state_changed:
            await update_status(name, namespace, "Running", "VyOS router container is running",
                               body=body, spec=spec, uid=kwargs.get('uid'), logger_obj=logger,
                               interfaces=status_interfaces if status_interfaces else None)
        else:
            logger.debug(f"VyOSRouter {name} state unchanged, skipping status update")
            
    except Exception as e:
        logger.error(f"Failed to monitor VyOSRouter {name}: {e}")

def _is_transient_error(error_msg: str) -> bool:
    """
    Determine if an error is transient (worth retrying) vs permanent.
    
    Transient errors include:
    - SSH/network connectivity issues to the VM
    - Docker daemon temporarily unavailable
    - Image pull timeouts or retryable failures
    - Container name conflict (previous partial creation - idempotent retry)
    - Ansible connection timeout
    """
    error_lower = error_msg.lower()
    transient_patterns = [
        'ssh',
        'connection refused',
        'connection timed out',
        'connection reset',
        'timeout',
        'unreachable',
        'temporarily unavailable',
        'docker daemon',
        'failed to execute ansible',
        'ansible playbook failed with status: failed',  # Generic Ansible failure - often transient
        'already in use',           # Container name conflict - idempotent retry
        'already exists',           # Container already exists - idempotent retry
        'no such host',
        'network is unreachable',
        'pulling image',
        'pull access denied',       # Docker pull issue - may be transient
        'toomanyrequests',          # Docker Hub rate limit
        'i/o timeout',
        'broken pipe',
        'eof',
    ]
    
    return any(pattern in error_lower for pattern in transient_patterns)


def _interfaces_changed(previous: list, current: list) -> bool:
    """Compare interface lists to detect changes"""
    if len(previous) != len(current):
        return True
    
    # Create comparable representations
    prev_set = set()
    curr_set = set()
    
    for iface in previous:
        # Create a hashable representation of interface
        key = (iface.get('name'), iface.get('status'), iface.get('linux_network'))
        prev_set.add(key)
    
    for iface in current:
        key = (iface.get('name'), iface.get('status'), iface.get('linux_network'))
        curr_set.add(key)
    
    return prev_set != curr_set

#########################################################################
# Status Management
#########################################################################
async def update_status(name: str, namespace: str, phase: str, message: str, 
                       body: Optional[Dict] = None, spec: Optional[Dict] = None, 
                       uid: Optional[str] = None, logger_obj: Optional[Any] = None,
                       container_id: Optional[str] = None, ip_address: Optional[str] = None,
                       interfaces: Optional[list] = None, applied_config: Optional[str] = None):
    """Update the status of a VyOSRouter resource in both Kubernetes and Spanner"""
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='VyOSRouter')
    
    resource = api.get(name=name, namespace=namespace)
    resource_dict = resource.to_dict()
    
    if 'status' not in resource_dict:
        resource_dict['status'] = {}
    
    status = {
        'phase': phase,
        'message': message
    }
    
    if container_id:
        status['container_id'] = container_id
    
    if ip_address:
        status['ip_address'] = ip_address
    
    if interfaces:
        status['interfaces'] = interfaces
    

    if applied_config:
        status['applied_config'] = applied_config
    
    resource_dict['status'].update(status)

    # Update Kubernetes status
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
            logger.warning(f"Status subresource not enabled for VyOSRouter {name}, skipping status update.")
        else:
            logger.error(f"Failed to update status for VyOSRouter {name}: {e}")
    
    # Sync to Spanner if parameters provided
    if body is not None and spec is not None and uid is not None and logger_obj is not None:
        try:
            # Create a modified copy of body with updated status for Spanner sync
            body_dict = dict(body)
            body_dict['status'] = status
            # Sync to Spanner
            await sync_physical_router(body_dict, spec, name, uid, logger_obj)
        except Exception as e:
            if logger_obj:
                logger_obj.error(f"Failed to sync status to Spanner for VyOSRouter {name}: {e}")
