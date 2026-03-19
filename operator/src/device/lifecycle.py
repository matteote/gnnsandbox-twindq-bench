import kopf
import logging
import kubernetes
from device.lifecycle_tasks import (
    create_device,
    delete_device,
)
from utils.compute import get_ip

logger = logging.getLogger(__name__)

#########################################################################
# Device Lifecycle Management
#########################################################################

@kopf.on.create('google.dev', 'v1', 'device')
async def create_device_handler(body, spec, name, namespace, uid, logger, **kwargs):
    """Handle Device creation using Ansible"""
    logger.info(f"Creating Device: {name} in namespace: {namespace}")
    logger.info(f"spec {spec}")

    networkvm_ip_address = await get_ip("automation", "networkvm")
    if networkvm_ip_address is None:
        raise kopf.TemporaryError("No ip address found on Network VM yet, temporary error - waiting", 10)
    logger.info(f"network vm address = {networkvm_ip_address}")

    # Update status to indicate creation has started
    await update_status(name, namespace, "Creating", "Creating Device")

    # Validate device name length
    if len(name) > 8:
        error_msg = "Device name must be 8 characters or fewer"
        logger.error(f"Device {name} creation failed: {error_msg}")
        await update_status(name, namespace, "Failed", error_msg)
        raise kopf.PermanentError(error_msg)

    # unique within namespace check
    # TODO
    
    # Check if the required LinuxNetwork CR is ready
    isready = await check_linux_network_ready(spec.get('network_name', ''), namespace)
    if not isready:
        error_msg = f"Waiting for LinuxNetwork to be ready"
        logger.warning(f"Device {name}: {error_msg}")            
        await update_status(name, namespace, "Pending", error_msg)

        # Raise temporary error to retry later
        raise kopf.TemporaryError(error_msg, delay=20)

    try:
        # Update status to indicate creation has started
        await update_status(name, namespace, "Creating", "Creating Device")

        # Create the Device using Ansible
        result = await create_device(networkvm_ip_address, name, spec)

        if result['success']:
            # Update status to ready with full details
            await update_status(
                name, namespace, "Ready", 
                f"Device {name} created successfully",
            )
            logger.info(f"Successfully created Device {name}")
        else:
            await update_status(name, namespace, "Failed", f"Failed to create Device: {result['error']}")
            raise kopf.PermanentError(f"Device creation failed: {result['error']}")

    except Exception as e:
        logger.error(f"Failed to create Device {name}: {e}")
        await update_status(name, namespace, "Failed", str(e))
        raise

@kopf.on.delete('google.dev', 'v1', 'device')
async def delete_device_handler(body, spec, name, namespace, logger, **kwargs):
    """Handle Device deletion using Ansible"""
    logger.info(f"Deleting Device: {name} in namespace: {namespace}")

    networkvm_ip_address = await get_ip("automation", "networkvm")
    if networkvm_ip_address is None:
        raise kopf.TemporaryError("No ip address found on Network VM yet, temporary error - waiting", 10)
    logger.info(f"network vm address = {networkvm_ip_address}")

    try:
        # Delete the Device container using Ansible
        result = await delete_device(networkvm_ip_address, name, spec)
        
        if result['success']:
            logger.info(f"Successfully deleted Device {name}")
        else:
            logger.warning(f"Failed to delete Device {name}: {result['error']}")
            # Don't raise error on delete failure - resource should still be removed from Kubernetes
            
    except Exception as e:
        logger.error(f"Error during Device deletion {name}: {e}")
        # Don't raise error on delete failure

#########################################################################
# LinuxNetwork Validation
#########################################################################

async def check_linux_network_ready(network_name: str, namespace: str) -> bool:
    """
    Check if the specified LinuxNetwork CR exists and has status 'Ready'.
    
    Args:
        network_name: Name of the LinuxNetwork resource
        namespace: Kubernetes namespace
    
    Returns:
        bool: True if network is ready, False otherwise
    """
    if not network_name:
        logger.warning("No network_name provided for LinuxNetwork check")
        return False
        
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='LinuxNetwork')
    
    try:
        network = api.get(name=network_name, namespace=namespace)
        network_dict = network.to_dict()
        
        status = network_dict.get('status', {})
        phase = status.get('phase', 'Unknown')
        
        if phase != 'Ready':
            logger.warning(f"LinuxNetwork '{network_name}' is in phase '{phase}', not Ready")
            return False
        else:
            logger.info(f"LinuxNetwork '{network_name}' is Ready")
            return True

    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            logger.warning(f"LinuxNetwork '{network_name}' not found")
            return False
        else:
            logger.error(f"Error checking LinuxNetwork '{network_name}': {e}")
            return False
    except Exception as e:
        logger.error(f"Unexpected error checking LinuxNetwork '{network_name}': {e}")
        return False

#########################################################################
# Status Management
#########################################################################

async def update_status(name: str, namespace: str, phase: str, message: str):
    """Update the status of a Device resource"""
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='Device')

    resource = api.get(name=name, namespace=namespace)
    resource_dict = resource.to_dict()

    if 'status' not in resource_dict:
        resource_dict['status'] = {}

    status = {
        'phase': phase,
        'message': message
    }
        
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
            logger.warning(f"Status subresource not enabled for Device {name}, skipping status update.")
        else:
            logger.error(f"Failed to update status for Device {name}: {e}")
