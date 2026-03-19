import kopf
import logging
from typing import Dict, Any
from utils.vyosnetwork import (
    validate_network_topology,
    generate_linux_networks,
    generate_vyos_routers,
    create_linux_network,
    create_vyos_router,
    update_status,
    check_and_update_parent_status
)
from vyosinfrastructure.lifecycle_tasks import (
    extract_routers_from_spec,
    update_opsagent_config,
)

logger = logging.getLogger(__name__)

@kopf.on.create('google.dev', 'v1', 'vyosinfrastructure')
async def create_vyosinfrastructure(body, spec, name, namespace, uid, logger, **kwargs):
    """Handle VyOSInfrastructure creation - VMs and basic networking"""
    logger.info(f"Creating VyOSInfrastructure: {name}")
    
    try:
        await update_status(name, namespace, "VyOSInfrastructure", "Validating", "Validating topology")
        
        validation_result = validate_network_topology(spec)
        if not validation_result['valid']:
            await update_status(name, namespace, "VyOSInfrastructure", "Error", validation_result['error'])
            raise kopf.PermanentError(validation_result['error'])

        await update_status(name, namespace, "VyOSInfrastructure", "Processing", "Generating resources")
        
        # Generate and create LinuxNetworks
        linux_networks = generate_linux_networks(spec, name, namespace, uid, "VyOSInfrastructure")
        for network in linux_networks:
            try:
                await create_linux_network(network, namespace)
            except Exception as e:
                logger.error(f"Failed to create LinuxNetwork {network['metadata']['name']}: {e}")

        # Generate and create VyOSRouters
        vyos_routers = generate_vyos_routers(spec, name, namespace, uid, "VyOSInfrastructure")
        created_routers = []
        for router in vyos_routers:
            try:
                await create_vyos_router(router, namespace)
                created_routers.append(router['metadata']['name'])
            except Exception as e:
                logger.error(f"Failed to create VyOSRouter {router['metadata']['name']}: {e}")

        await update_status(name, namespace, "VyOSInfrastructure", "Creating", 
                          f"Created {len(linux_networks)} networks and {len(created_routers)} routers",
                          networks=[n['metadata']['name'] for n in linux_networks],
                          routers=created_routers)

        # Update the Ops Agent on the monitoring VM so it scrapes the new routers
        routers = extract_routers_from_spec(spec)
        await update_opsagent_config(namespace, routers)

    except Exception as e:
        error_msg = f"Failed to create VyOSInfrastructure: {str(e)}"
        logger.error(error_msg)
        await update_status(name, namespace, "VyOSInfrastructure", "Error", error_msg)
        raise kopf.PermanentError(error_msg)

@kopf.on.resume('google.dev', 'v1', 'vyosinfrastructure')
async def resume_vyosinfrastructure(body, spec, name, namespace, logger, **kwargs):
    """Re-sync the Ops Agent config when the operator restarts against existing resources."""
    logger.info(f"Resuming VyOSInfrastructure: {name} - syncing Ops Agent config")
    routers = extract_routers_from_spec(spec)
    await update_opsagent_config(namespace, routers)

@kopf.on.update('google.dev', 'v1', 'vyosinfrastructure')
async def update_vyosinfrastructure(body, spec, name, namespace, uid, logger, **kwargs):
    logger.info(f"Updating VyOSInfrastructure: {name}")
    # Reuse creation logic to reconcile resources
    await create_vyosinfrastructure(body, spec, name, namespace, uid, logger, **kwargs)

@kopf.on.delete('google.dev', 'v1', 'vyosinfrastructure')
async def delete_vyosinfrastructure(body, spec, name, namespace, logger, **kwargs):
    """Handle VyOSInfrastructure deletion - remove router scrape targets from Ops Agent"""
    logger.info(f"Deleting VyOSInfrastructure: {name}")
    # Pass an empty router list to clear the Prometheus scrape config
    await update_opsagent_config(namespace, [])

@kopf.on.event('google.dev', 'v1', 'vyosrouter')
async def on_vyosrouter_status_change(body, spec, name, namespace, logger, **kwargs):
    """Watch for VyOSRouter status changes and update parent status"""
    # This handler needs to check if the router belongs to a VyOSInfrastructure
    # and if so, trigger a status check on the parent.
    
    owner_references = body.get('metadata', {}).get('ownerReferences', [])
    for owner in owner_references:
        if owner.get('kind') == 'VyOSInfrastructure':
            await check_and_update_parent_status(owner.get('name'), 'VyOSInfrastructure', namespace, logger)
