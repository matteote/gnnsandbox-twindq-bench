import kopf
import logging
import kubernetes
from typing import Dict, Any
from utils.vyosnetwork import patch_vyos_router, update_status

logger = logging.getLogger(__name__)

async def check_underlay_ready(underlay_ref: str, namespace: str, logger) -> bool:
    """Check if the referenced VyOSUnderlay is ready"""
    if not underlay_ref:
        logger.warning("No underlayRef specified in VyOSL3VPN spec")
        return True  # Allow creation if no underlay is referenced
    
    try:
        client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
        api = client.resources.get(api_version='google.dev/v1', kind='VyOSUnderlay')
        
        underlay = api.get(name=underlay_ref, namespace=namespace)
        underlay_status = underlay.get('status', {})
        phase = underlay_status.get('phase', 'Unknown')
        
        logger.info(f"VyOSUnderlay {underlay_ref} status: {phase}")
        
        if phase == 'Ready':
            return True
        else:
            logger.info(f"Waiting for VyOSUnderlay {underlay_ref} to be Ready (current: {phase})")
            return False
            
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            logger.error(f"Referenced VyOSUnderlay {underlay_ref} not found")
            raise kopf.PermanentError(f"VyOSUnderlay {underlay_ref} not found")
        else:
            logger.error(f"Error checking VyOSUnderlay {underlay_ref}: {e}")
            raise

@kopf.on.create('google.dev', 'v1', 'vyosl3vpn')
async def create_vyosl3vpn(body, spec, name, namespace, uid, logger, **kwargs):
    """Handle VyOSL3VPN creation - Service/VRF patch"""
    logger.info(f"Creating VyOSL3VPN: {name}")
    
    try:
        # Check if the referenced underlay is ready before proceeding
        underlay_ref = spec.get('underlayRef')
        if not await check_underlay_ready(underlay_ref, namespace, logger):
            await update_status(name, namespace, "VyOSL3VPN", "Waiting", 
                              f"Waiting for VyOSUnderlay {underlay_ref} to be ready")
            raise kopf.TemporaryError(f"Waiting for VyOSUnderlay {underlay_ref} to be ready", delay=10)
        
        await update_status(name, namespace, "VyOSL3VPN", "Processing", "Applying L3VPN configuration")
        
        routers = spec.get('routers', [])
        
        for router in routers:
            router_name = router['name']
            
            # Construct the patch for the VyOSRouter
            # We merge the VRF and BGP configurations
            vrfs = router.get('vrfs', [])
            
            router_patch = {
                'spec': {
                    'vrfs': vrfs,
                    'protocols': {
                        'bgp': router.get('bgp', {})
                    }
                }
            }
            
            try:
                # First, apply VRF and BGP configuration
                await patch_vyos_router(router_name, namespace, router_patch)
                
                # Then, update interface VRF assignments using JSON patch
                # Get current router to find interfaces
                for vrf in vrfs:
                    vrf_name = vrf['name']
                    vrf_interfaces = vrf.get('interfaces', [])
                    
                    if vrf_interfaces:
                        # Get the current router spec to find interface indices
                        client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
                        api = client.resources.get(api_version='google.dev/v1', kind='VyOSRouter')
                        current_router = api.get(name=router_name, namespace=namespace)
                        current_interfaces = current_router.get('spec', {}).get('interfaces', [])
                        
                        # Build JSON patch operations for each interface that needs VRF assignment
                        json_patch = []
                        for intf_name in vrf_interfaces:
                            # Find the index of this interface
                            for idx, intf in enumerate(current_interfaces):
                                if intf.get('name') == intf_name:
                                    json_patch.append({
                                        'op': 'add',
                                        'path': f'/spec/interfaces/{idx}/vrf',
                                        'value': vrf_name
                                    })
                                    break
                        
                        # Apply JSON patch if we have operations
                        if json_patch:
                            import json
                            api.patch(
                                name=router_name,
                                namespace=namespace,
                                body=json_patch,
                                content_type='application/json-patch+json'
                            )
                            logger.info(f"Assigned interfaces {vrf_interfaces} to VRF {vrf_name} on router {router_name}")
                        
            except Exception as e:
                logger.error(f"Failed to patch router {router_name} for L3VPN: {e}")
        
        await update_status(name, namespace, "VyOSL3VPN", "Ready", "L3VPN configuration applied")

    except kopf.TemporaryError:
        # Re-raise TemporaryError to allow Kopf to retry
        raise
    except kopf.PermanentError:
        # Re-raise PermanentError from check_underlay_ready
        raise
    except Exception as e:
        error_msg = f"Failed to create VyOSL3VPN: {str(e)}"
        logger.error(error_msg)
        await update_status(name, namespace, "VyOSL3VPN", "Error", error_msg)
        raise kopf.PermanentError(error_msg)

@kopf.on.update('google.dev', 'v1', 'vyosl3vpn')
async def update_vyosl3vpn(body, spec, name, namespace, uid, logger, **kwargs):
    logger.info(f"Updating VyOSL3VPN: {name}")
    # Re-apply configuration
    await create_vyosl3vpn(body, spec, name, namespace, uid, logger, **kwargs)
