import asyncio
import kopf
import logging
import kubernetes
from typing import Dict, Any
from utils.vyosnetwork import patch_vyos_router, update_status

logger = logging.getLogger(__name__)


async def _wait_for_routers_leave_running(
    router_names: list, namespace: str, logger,
    timeout: int = 30, poll_interval: int = 3
) -> None:
    """Wait until every named router transitions AWAY from Running state.

    After a spec patch is written to the K8s API there is a brief window
    before kopf's update_vyosrouter handler fires and changes the router
    status to Updating.  If _wait_for_routers_running is called before that
    transition happens it will see stale Running status and return immediately,
    marking the underlay Ready while Ansible is still in-flight.

    This helper closes that race by blocking until each router's phase is no
    longer Running (confirming the update handler has started).  A short
    timeout is intentional — if a router stays Running for the full window
    it most likely means Ansible completed before our first poll; we log a
    warning and let the caller continue to _wait_for_routers_running.

    Raises kopf.PermanentError if any router enters Failed state.
    """
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='VyOSRouter')

    still_running = list(router_names)
    elapsed = 0
    while elapsed < timeout and still_running:
        next_still_running = []
        for rname in still_running:
            try:
                r = api.get(name=rname, namespace=namespace)
                phase = r.get('status', {}).get('phase', 'Unknown')
                if phase == 'Failed':
                    raise kopf.PermanentError(
                        f"Router {rname} entered Failed state while applying underlay configuration"
                    )
                if phase == 'Running':
                    next_still_running.append(rname)
                # Any non-Running, non-Failed phase means the handler picked it up — good.
            except kopf.PermanentError:
                raise
            except Exception as e:
                # If we can't read the router, conservatively assume still Running.
                next_still_running.append(f"{rname}(error:{e})")

        still_running = next_still_running
        if still_running:
            logger.info(
                f"Waiting for routers to leave Running state (update handler not yet active): "
                f"{still_running} ({elapsed}s elapsed)"
            )
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

    if still_running:
        logger.warning(
            f"Routers did not transition away from Running within {timeout}s: {still_running}. "
            f"Ansible may have completed before the first poll — proceeding to Running wait."
        )
    else:
        logger.info(f"All routers have left Running state, update handlers are active: {router_names}")


async def _wait_for_routers_running(
    router_names: list, namespace: str, logger,
    timeout: int = 180, poll_interval: int = 5
) -> None:
    """Poll VyOSRouter CRs until every named router reaches Running status.

    Raises kopf.PermanentError if any router enters Failed state or the
    overall timeout expires.  Uses asyncio.sleep so the event loop stays
    responsive while we wait.
    """
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='VyOSRouter')

    elapsed = 0
    while elapsed < timeout:
        not_running = []
        for rname in router_names:
            try:
                r = api.get(name=rname, namespace=namespace)
                phase = r.get('status', {}).get('phase', 'Unknown')
                if phase == 'Failed':
                    raise kopf.PermanentError(
                        f"Router {rname} entered Failed state while applying underlay configuration"
                    )
                if phase != 'Running':
                    not_running.append(f"{rname}({phase})")
            except kopf.PermanentError:
                raise
            except Exception as e:
                not_running.append(f"{rname}(error:{e})")

        if not not_running:
            logger.info(f"All routers reached Running state: {router_names}")
            return

        logger.info(f"Waiting for routers to reach Running: {not_running} ({elapsed}s elapsed)")
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

    raise kopf.PermanentError(
        f"Timed out after {timeout}s waiting for routers to reach Running state: {not_running}"
    )


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
        patched_routers = []
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
                patched_routers.append(router_name)
            except Exception as e:
                logger.error(f"Failed to patch router {router_name} for underlay: {e}")
                # We continue with other routers even if one fails

        # Wait for every patched router to finish its Ansible reconfiguration
        # and reach Running status before marking the underlay Ready.
        if patched_routers:
            await update_status(name, namespace, "VyOSUnderlay", "Processing",
                                f"Waiting for routers to finish reconfiguration: {patched_routers}")
            # Phase 1: wait for routers to leave Running so we know the
            # update handler has picked up the spec change.
            await _wait_for_routers_leave_running(patched_routers, namespace, logger)
            # Phase 2: wait for routers to return to Running (Ansible done).
            await _wait_for_routers_running(patched_routers, namespace, logger)

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


@kopf.on.delete('google.dev', 'v1', 'vyosunderlay')
async def delete_vyosunderlay(body, spec, name, namespace, logger, **kwargs):
    """Handle VyOSUnderlay deletion — clear underlay config from all affected routers.

    For each router listed in spec.routers, clears the protocols and
    traffic_policy fields that were applied by this underlay, then waits for
    every router to finish its Ansible reconfiguration before allowing kopf to
    release the finalizer.

    Errors are logged but not re-raised so that the CR is still removed from
    Kubernetes even if a router fails mid-cleanup.
    """
    logger.info(f"Deleting VyOSUnderlay: {name} — clearing underlay config from routers")

    routers = spec.get('routers', [])
    if not routers:
        logger.info(f"VyOSUnderlay {name}: no routers listed in spec, nothing to clean up")
        return

    patched_routers = []
    for router in routers:
        router_name = router.get('name')
        if not router_name:
            continue

        # Clear the underlay-applied fields so the router reconciles to its
        # baseline config. Setting protocols to {} removes underlay protocol
        # config; traffic_policy is cleared the same way.
        router_patch = {
            'spec': {
                'protocols': {},
            }
        }
        if 'traffic_policy' in router:
            router_patch['spec']['traffic_policy'] = {}

        try:
            await patch_vyos_router(router_name, namespace, router_patch)
            patched_routers.append(router_name)
            logger.info(f"Cleared underlay config from VyOSRouter {router_name}")
        except Exception as e:
            logger.error(f"Failed to clear underlay config from VyOSRouter {router_name}: {e}")

    # Wait for every patched router to finish its Ansible reconfiguration
    # before releasing the finalizer.
    if patched_routers:
        try:
            logger.info(
                f"VyOSUnderlay {name} delete: waiting for routers to finish "
                f"reconfiguration: {patched_routers}"
            )
            # Phase 1: wait for routers to leave Running so we know the
            # update handler has picked up the spec change.
            await _wait_for_routers_leave_running(patched_routers, namespace, logger)
            # Phase 2: wait for routers to return to Running (Ansible done).
            await _wait_for_routers_running(patched_routers, namespace, logger)
        except kopf.PermanentError:
            # Log but don't block deletion — the CR should still be removed.
            logger.error(
                f"Permanent error while waiting for routers during VyOSUnderlay {name} "
                f"cleanup; router config may need manual inspection"
            )
        except Exception as e:
            logger.error(
                f"Unexpected error while waiting for routers during VyOSUnderlay {name} "
                f"cleanup: {e}",
                exc_info=True,
            )

    logger.info(f"VyOSUnderlay {name}: underlay config cleared from all routers")
