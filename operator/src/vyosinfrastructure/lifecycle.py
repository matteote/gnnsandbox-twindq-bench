import kopf
import logging
import kubernetes
from typing import Dict, Any
from utils.vyosnetwork import (
    validate_network_topology,
    generate_linux_networks,
    generate_vyos_routers,
    generate_devices,
    create_linux_network,
    create_vyos_router,
    create_device as create_device_cr,
    update_status,
    check_and_update_parent_status
)
from vyosinfrastructure.lifecycle_tasks import (
    extract_routers_from_spec,
    extract_devices_from_spec,
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
        
        # Generate and create LinuxNetworks.
        # Track failures separately: if any network is still terminating (detected
        # by create_linux_network raising RuntimeError) the handler raises a
        # TemporaryError so Kopf retries the whole create after a short delay.
        linux_networks = generate_linux_networks(spec, name, namespace, uid, "VyOSInfrastructure")
        terminating_networks: list[str] = []
        for network in linux_networks:
            try:
                await create_linux_network(network, namespace)
            except RuntimeError as e:
                # create_linux_network raises RuntimeError when a 409 is returned
                # AND the existing resource still has a deletionTimestamp.
                logger.warning(f"LinuxNetwork {network['metadata']['name']} is still terminating; will retry: {e}")
                terminating_networks.append(network['metadata']['name'])
            except Exception as e:
                logger.error(f"Failed to create LinuxNetwork {network['metadata']['name']}: {e}")

        if terminating_networks:
            raise kopf.TemporaryError(
                f"Waiting for terminating LinuxNetworks to be fully removed before re-creating: "
                f"{terminating_networks}",
                delay=10,
            )

        # Generate and create VyOSRouters
        vyos_routers = generate_vyos_routers(spec, name, namespace, uid, "VyOSInfrastructure")
        created_routers = []
        for router in vyos_routers:
            try:
                await create_vyos_router(router, namespace)
                created_routers.append(router['metadata']['name'])
            except Exception as e:
                logger.error(f"Failed to create VyOSRouter {router['metadata']['name']}: {e}")

        # Generate and create Devices (each gets mgmt_ip + data-plane IP from spec)
        device_crs = generate_devices(spec, name, namespace, uid, "VyOSInfrastructure")
        created_devices = []
        for device_cr in device_crs:
            try:
                await create_device_cr(device_cr, namespace)
                created_devices.append(device_cr['metadata']['name'])
            except Exception as e:
                logger.error(f"Failed to create Device {device_cr['metadata']['name']}: {e}")

        await update_status(name, namespace, "VyOSInfrastructure", "Creating",
                          f"Created {len(linux_networks)} networks, {len(created_routers)} routers, "
                          f"and {len(created_devices)} devices",
                          networks=[n['metadata']['name'] for n in linux_networks],
                          routers=created_routers,
                          devices=created_devices)

        # Update the Ops Agent on the monitoring VM so it scrapes the new routers
        # and traffic-agent Prometheus endpoints on each device.
        routers = extract_routers_from_spec(spec)
        devices = extract_devices_from_spec(spec)
        await update_opsagent_config(namespace, routers, devices)

    except (kopf.TemporaryError, kopf.PermanentError):
        # Re-raise kopf control errors without wrapping them.
        raise
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
    devices = extract_devices_from_spec(spec)
    await update_opsagent_config(namespace, routers, devices)

@kopf.on.update('google.dev', 'v1', 'vyosinfrastructure')
async def update_vyosinfrastructure(body, spec, name, namespace, uid, logger, **kwargs):
    logger.info(f"Updating VyOSInfrastructure: {name}")
    # Reuse creation logic to reconcile resources
    await create_vyosinfrastructure(body, spec, name, namespace, uid, logger, **kwargs)

# Child CRD kinds owned by VyOSInfrastructure via ownerReferences.
# Checked in dependency order (VyOSRouter before LinuxNetwork/Device so that
# the most "expensive" resource is confirmed gone first).
_INFRA_CHILD_KINDS = ["VyOSRouter", "LinuxNetwork", "Device"]


def _list_owned_children(uid: str, namespace: str) -> list[str]:
    """
    Return the names of child CRs that still have *uid* in their ownerReferences.

    Uses the kubernetes dynamic client so it works for any CRD group/version
    without hard-coding plural names.

    Args:
        uid:       UID of the parent VyOSInfrastructure.
        namespace: Namespace to search.

    Returns:
        list of "Kind/name" strings for CRs that still exist and are owned by *uid*.
    """
    remaining: list[str] = []
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())

    for kind in _INFRA_CHILD_KINDS:
        try:
            api = client.resources.get(api_version="google.dev/v1", kind=kind)
            items = api.get(namespace=namespace)
            cr_list = items.items if hasattr(items, "items") else []
            for cr in cr_list:
                cr_dict = cr.to_dict() if hasattr(cr, "to_dict") else cr
                owners = cr_dict.get("metadata", {}).get("ownerReferences") or []
                if any(ref.get("uid") == uid for ref in owners):
                    remaining.append(f"{kind}/{cr_dict['metadata']['name']}")
        except kubernetes.client.rest.ApiException as exc:
            if exc.status == 404:
                # CRD not installed — no children of this kind can exist.
                pass
            else:
                raise

    return remaining


@kopf.on.delete('google.dev', 'v1', 'vyosinfrastructure')
async def delete_vyosinfrastructure(body, spec, name, namespace, logger, **kwargs):
    """Handle VyOSInfrastructure deletion.

    Steps:
      1. Clear all Ops Agent Prometheus scrape targets (routers + devices).
      2. Actively delete every owned child CR (VyOSRouter, LinuxNetwork,
         Device) that has not already been marked for deletion.  We cannot
         rely on Kubernetes garbage collection here because GC only removes
         owned resources after the parent is *fully gone from etcd*, but
         Kopf's finalizer keeps the parent in etcd until this handler
         succeeds — creating a deadlock if we merely wait for GC.
      3. Raise a kopf.TemporaryError to re-run the handler until all child
         CRs have been confirmed gone (their own delete handlers run
         asynchronously and may take time).
    """
    logger.info(f"Deleting VyOSInfrastructure: {name}")
    await update_status(name, namespace, "VyOSInfrastructure", "Deleting", "Deleting VyOSInfrastructure")

    # Step 1 — clear OpsAgent scrape targets immediately.
    await update_opsagent_config(namespace, [], [])

    # Step 2 — actively issue DELETE for every owned child that is not yet
    # terminating.  Children that already have a deletionTimestamp are already
    # on their way out; we just need to wait for them.
    uid = body["metadata"]["uid"]
    dyn_client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())

    for kind in _INFRA_CHILD_KINDS:
        try:
            api = dyn_client.resources.get(api_version="google.dev/v1", kind=kind)
            items = api.get(namespace=namespace)
            cr_list = items.items if hasattr(items, "items") else []
            for cr in cr_list:
                cr_dict = cr.to_dict() if hasattr(cr, "to_dict") else cr
                owners = cr_dict.get("metadata", {}).get("ownerReferences") or []
                if not any(ref.get("uid") == uid for ref in owners):
                    continue  # not owned by this VyOSInfrastructure
                child_name = cr_dict["metadata"]["name"]
                # Skip if already terminating (deletionTimestamp set)
                if cr_dict.get("metadata", {}).get("deletionTimestamp"):
                    continue
                try:
                    api.delete(name=child_name, namespace=namespace)
                    logger.info(f"Issued delete for {kind}/{child_name}")
                except kubernetes.client.rest.ApiException as del_exc:
                    if del_exc.status == 404:
                        pass  # Already gone
                    else:
                        logger.warning(
                            f"Failed to delete {kind}/{child_name}: {del_exc}"
                        )
        except kubernetes.client.rest.ApiException as list_exc:
            if list_exc.status == 404:
                pass  # CRD not installed — no children of this kind
            else:
                logger.warning(
                    f"Could not list {kind} resources for deletion: {list_exc}"
                )
        except Exception as kind_exc:
            logger.error(
                f"Unexpected error while deleting {kind} children: {kind_exc}",
                exc_info=True,
            )

    # Step 3 — wait for all owned children to be fully gone.
    try:
        remaining = _list_owned_children(uid, namespace)
    except Exception as exc:
        logger.error(
            f"Error listing children of VyOSInfrastructure {name}: {exc}"
        )
        raise kopf.TemporaryError(
            f"Error listing children: {exc}", delay=5
        ) from exc

    if remaining:
        logger.info(
            f"VyOSInfrastructure {name}: waiting for {len(remaining)} "
            f"child CR(s) to be deleted: {remaining}"
        )
        raise kopf.TemporaryError(
            f"Waiting for {len(remaining)} child CR(s): {remaining}",
            delay=5,
        )

    logger.info(
        f"VyOSInfrastructure {name}: all owned children are gone. "
        "Finalizer will now be released."
    )

@kopf.on.event('google.dev', 'v1', 'vyosrouter')
async def on_vyosrouter_status_change(body, spec, name, namespace, logger, **kwargs):
    """Watch for VyOSRouter status changes and update parent status"""
    # This handler needs to check if the router belongs to a VyOSInfrastructure
    # and if so, trigger a status check on the parent.
    
    owner_references = body.get('metadata', {}).get('ownerReferences', [])
    for owner in owner_references:
        if owner.get('kind') == 'VyOSInfrastructure':
            await check_and_update_parent_status(owner.get('name'), 'VyOSInfrastructure', namespace, logger)

@kopf.on.event('google.dev', 'v1', 'device')
async def on_device_status_change(body, spec, name, namespace, logger, **kwargs):
    """Watch for Device status changes and propagate to parent VyOSInfrastructure"""
    owner_references = body.get('metadata', {}).get('ownerReferences', [])
    for owner in owner_references:
        if owner.get('kind') == 'VyOSInfrastructure':
            await check_and_update_parent_status(owner.get('name'), 'VyOSInfrastructure', namespace, logger)
