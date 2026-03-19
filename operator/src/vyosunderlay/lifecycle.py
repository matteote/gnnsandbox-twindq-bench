import kopf
import logging
import kubernetes
from typing import Dict, Any
from utils.vyosnetwork import patch_vyos_router, update_status

logger = logging.getLogger(__name__)

async def check_infrastructure_ready(infrastructure_ref: str, namespace: str, logger) -> bool:
    """Check if the referenced VyOSInfrastructure is ready"""
    if not infrastructure_ref:
        logger.warning("No infrastructureRef specified in VyOSUnderlay spec")
        return True  # Allow creation if no infrastructure is referenced
    
    try:
        client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
        api = client.resources.get(api_version='google.dev/v1', kind='VyOSInfrastructure')
        
        infrastructure = api.get(name=infrastructure_ref, namespace=namespace)
        infrastructure_status = infrastructure.get('status', {})
        phase = infrastructure_status.get('phase', 'Unknown')
        
        logger.info(f"VyOSInfrastructure {infrastructure_ref} status: {phase}")
        
        if phase == 'Ready':
            return True
        else:
            logger.info(f"Waiting for VyOSInfrastructure {infrastructure_ref} to be Ready (current: {phase})")
            return False
            
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            logger.error(f"Referenced VyOSInfrastructure {infrastructure_ref} not found")
            raise kopf.PermanentError(f"VyOSInfrastructure {infrastructure_ref} not found")
        else:
            logger.error(f"Error checking VyOSInfrastructure {infrastructure_ref}: {e}")
            raise

@kopf.on.create('google.dev', 'v1', 'vyosunderlay')
async def create_vyosunderlay(body, spec, name, namespace, uid, logger, **kwargs):
    """Handle VyOSUnderlay creation - Core protocols patch"""
    logger.info(f"Creating VyOSUnderlay: {name}")
    
    try:
        # Check if the referenced infrastructure is ready before proceeding
        infrastructure_ref = spec.get('infrastructureRef')
        if not await check_infrastructure_ready(infrastructure_ref, namespace, logger):
            await update_status(name, namespace, "VyOSUnderlay", "Waiting", 
                              f"Waiting for VyOSInfrastructure {infrastructure_ref} to be ready")
            raise kopf.TemporaryError(f"Waiting for VyOSInfrastructure {infrastructure_ref} to be ready", delay=10)
        
        await update_status(name, namespace, "VyOSUnderlay", "Processing", "Applying underlay configuration")
        
        routers = spec.get('routers', [])
        
        # Apply global protocol settings to all routers in the infrastructure
        # (This is a simplification, in reality we might want to be more selective)
        
        for router in routers:
            router_name = router['name']
            
            # Construct the patch for the VyOSRouter
            # We merge the underlay protocols into the router's spec.protocols
            router_patch = {
                'spec': {
                    'protocols': router.get('protocols', {})
                }
            }
            if 'traffic_policy' in router:
                router_patch['spec']['traffic_policy'] = router['traffic_policy']
            
            try:
                await patch_vyos_router(router_name, namespace, router_patch)
            except Exception as e:
                logger.error(f"Failed to patch router {router_name} for underlay: {e}")
                # We continue with other routers even if one fails
        
        await update_status(name, namespace, "VyOSUnderlay", "Ready", "Underlay configuration applied")

    except kopf.TemporaryError:
        # Re-raise TemporaryError to allow Kopf to retry
        raise
    except kopf.PermanentError:
        # Re-raise PermanentError from check_infrastructure_ready
        raise
    except Exception as e:
        error_msg = f"Failed to create VyOSUnderlay: {str(e)}"
        logger.error(error_msg)
        await update_status(name, namespace, "VyOSUnderlay", "Error", error_msg)
        raise kopf.PermanentError(error_msg)

@kopf.on.update('google.dev', 'v1', 'vyosunderlay')
async def update_vyosunderlay(body, spec, name, namespace, uid, logger, **kwargs):
    logger.info(f"Updating VyOSUnderlay: {name}")
    # Re-apply configuration
    await create_vyosunderlay(body, spec, name, namespace, uid, logger, **kwargs)
