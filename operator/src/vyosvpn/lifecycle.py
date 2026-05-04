import asyncio
import kopf
import logging
import kubernetes
from typing import Dict, Any
from utils.vyosnetwork import patch_vyos_router, update_status

logger = logging.getLogger(__name__)

# Serialize all VyOSL3VPN reconciliations so that concurrent VPN CRDs
# (e.g. blue-vpn and red-vpn applied back-to-back from the dashboard)
# don't race on the same PE router patches and cause rr2 to fail
# mid-configuration. asyncio.Lock yields control back to the event loop
# while waiting, so the operator remains responsive.
_vpn_reconcile_lock = asyncio.Lock()

async def _wait_for_routers_leave_running(
    router_names: list, namespace: str, logger,
    timeout: int = 30, poll_interval: int = 3
) -> None:
    """Wait until every named router transitions AWAY from Running state.

    After a spec patch is written to the K8s API there is a brief window
    before kopf's update_vyosrouter handler fires and changes the router
    status to Updating.  If _wait_for_routers_running is called before that
    transition happens it will see stale Running status and return immediately,
    releasing the VPN reconcile lock too early.

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
                    raise kopf.TemporaryError(
                        f"Router {rname} entered Failed state while applying VPN configuration; "
                        f"will retry after router is recovered",
                        delay=60
                    )
                if phase == 'Running':
                    next_still_running.append(rname)
                # Any non-Running, non-Failed phase means the handler picked it up — good.
            except (kopf.TemporaryError, kopf.PermanentError):
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
                    raise kopf.TemporaryError(
                        f"Router {rname} entered Failed state while applying VPN configuration; "
                        f"will retry after router is recovered",
                        delay=60
                    )
                if phase != 'Running':
                    not_running.append(f"{rname}({phase})")
            except (kopf.TemporaryError, kopf.PermanentError):
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

    # Check underlay readiness BEFORE acquiring the lock so that temporary
    # retry waits don't block other VPN reconciliations unnecessarily.
    try:
        underlay_ref = spec.get('underlayRef')
        if not await check_underlay_ready(underlay_ref, namespace, logger):
            await update_status(name, namespace, "VyOSL3VPN", "Waiting",
                                f"Waiting for VyOSUnderlay {underlay_ref} to be ready")
            raise kopf.TemporaryError(
                f"Waiting for VyOSUnderlay {underlay_ref} to be ready", delay=10)
    except kopf.TemporaryError:
        raise
    except kopf.PermanentError:
        raise

    # Serialize the actual patch work so that concurrent VPN CRDs (e.g.
    # blue-vpn and red-vpn applied back-to-back by the dashboard) do not
    # race on the same PE routers and cause rr2 to fail mid-configuration.
    logger.info(f"VyOSL3VPN {name}: waiting for reconcile lock")
    async with _vpn_reconcile_lock:
        logger.info(f"VyOSL3VPN {name}: acquired reconcile lock, proceeding")
        try:
            await update_status(name, namespace, "VyOSL3VPN", "Processing", "Applying L3VPN configuration")

            routers = spec.get('routers', [])

            for router in routers:
                router_name = router['name']

                # VRFs and BGP config declared by this VPN for this router
                vrfs = router.get('vrfs', [])
                vpn_bgp = router.get('bgp', {})

                try:
                    import json as _json

                    # --- Merge VRFs and BGP VRFs with the existing router spec ---
                    # Using a Kubernetes merge-patch on a list field *replaces* the
                    # entire list.  We must therefore read the current spec first and
                    # accumulate VRFs from all VPNs so that applying VPN-B does not
                    # silently discard VPN-A's VRFs from the stored K8s spec (and
                    # consequently from Spanner and the UI).
                    #
                    # IMPORTANT: We convert the Kubernetes object to a plain Python
                    # dict via JSON round-trip BEFORE doing any merging.  This avoids
                    # two classes of bugs:
                    #   1. K8s ResourceField objects don't always serialize correctly
                    #      when put back into a patch body.
                    #   2. .get('key', default) only uses the default when the key is
                    #      *absent*; if the field exists but is null (e.g. vrfs: null),
                    #      .get() returns None, causing TypeErrors in set/list
                    #      comprehensions.  Plain Python dicts from JSON deserialization
                    #      preserve null as None, which our `or []` guards handle safely.
                    router_k8s_client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
                    router_api = router_k8s_client.resources.get(api_version='google.dev/v1', kind='VyOSRouter')
                    current_router = router_api.get(name=router_name, namespace=namespace)

                    # Serialize to plain Python dict via the API client's sanitizer
                    # so all nested ResourceField objects become standard dicts/lists.
                    _sanitized = kubernetes.client.ApiClient().sanitize_for_serialization(
                        current_router.to_dict()
                    )
                    current_spec = _json.loads(_json.dumps(_sanitized)).get('spec') or {}

                    # VRF merge: keep every VRF not owned by this VPN, then add/replace
                    # this VPN's VRFs (identified by name).
                    # null-safe: use `or []` in case the field is present but null.
                    current_vrfs = current_spec.get('vrfs') or []
                    # Ensure vrfs from VPN spec is also null-safe
                    vpn_vrfs = vrfs or []
                    vpn_vrf_names = {v['name'] for v in vpn_vrfs}
                    merged_vrfs = [v for v in current_vrfs if v.get('name') not in vpn_vrf_names]
                    merged_vrfs.extend(vpn_vrfs)

                    # BGP merge: preserve global BGP settings (AS number, global
                    # neighbors, route-reflector flag, etc.) and only replace the
                    # VRF-specific BGP entries that belong to this VPN.
                    # null-safe: use `or {}` / `or []` throughout.
                    current_bgp = (current_spec.get('protocols') or {}).get('bgp') or {}
                    vpn_bgp_vrfs = (vpn_bgp.get('vrfs') or []) if vpn_bgp else []
                    vpn_bgp_vrf_names = {v['name'] for v in vpn_bgp_vrfs}
                    existing_bgp_vrfs = [
                        v for v in (current_bgp.get('vrfs') or [])
                        if v.get('name') not in vpn_bgp_vrf_names
                    ]
                    merged_bgp = dict(current_bgp)
                    merged_bgp['vrfs'] = existing_bgp_vrfs + vpn_bgp_vrfs

                    logger.info(
                        f"Router {router_name}: merging VRFs "
                        f"existing={[v.get('name') for v in current_vrfs]} "
                        f"adding={[v.get('name') for v in vpn_vrfs]} "
                        f"→ merged={[v.get('name') for v in merged_vrfs]}"
                    )

                    # Construct the patch with the fully-merged VRF and BGP config
                    router_patch = {
                        'spec': {
                            'vrfs': merged_vrfs,
                            'protocols': {
                                'bgp': merged_bgp
                            }
                        }
                    }

                    # First, apply merged VRF and BGP configuration
                    await patch_vyos_router(router_name, namespace, router_patch)

                    # Then, update interface VRF assignments using JSON patch.
                    # Re-read the router after the merge-patch so that interface
                    # indices reflect the latest spec (including any changes made by
                    # the patch above).
                    for vrf in vpn_vrfs:
                        vrf_name = vrf['name']
                        vrf_interfaces = vrf.get('interfaces') or []

                        if vrf_interfaces:
                            # Re-read router to get up-to-date interface list / indices
                            client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
                            api = client.resources.get(api_version='google.dev/v1', kind='VyOSRouter')
                            current_router = api.get(name=router_name, namespace=namespace)
                            current_interfaces = current_router.get('spec', {}).get('interfaces') or []

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

            # Apply CE router eBGP configuration
            ce_routers = spec.get('ce_routers', [])
            for ce_router in ce_routers:
                ce_router_name = ce_router['name']
                ce_patch = {
                    'spec': {
                        'protocols': ce_router.get('protocols', {})
                    }
                }
                try:
                    await patch_vyos_router(ce_router_name, namespace, ce_patch)
                    logger.info(f"Applied CE eBGP config to {ce_router_name}")
                except Exception as e:
                    logger.error(f"Failed to patch CE router {ce_router_name}: {e}")

            # Wait for every patched router (PE + CE) to finish its Ansible
            # reconfiguration and reach Running status before releasing the
            # lock.  This prevents the next VPN from starting its patches
            # while the current VPN's Ansible playbooks are still in-flight
            # on the same routers (which was causing rr2 to fail).
            all_patched_routers = (
                [r['name'] for r in routers] +
                [r['name'] for r in ce_routers]
            )
            if all_patched_routers:
                await update_status(
                    name, namespace, "VyOSL3VPN", "Processing",
                    f"Waiting for routers to finish reconfiguration: {all_patched_routers}"
                )
                # Phase 1: wait for routers to leave Running so we know the
                # update handler has picked up the spec change.  Without this,
                # the phase-2 poll below can see stale Running status from
                # before the patch was processed and exit immediately, releasing
                # the lock while Ansible is still in-flight on the routers.
                await _wait_for_routers_leave_running(all_patched_routers, namespace, logger)
                # Phase 2: wait for routers to return to Running (Ansible done).
                await _wait_for_routers_running(all_patched_routers, namespace, logger)

            await update_status(name, namespace, "VyOSL3VPN", "Ready", "L3VPN configuration applied")
            logger.info(f"VyOSL3VPN {name}: releasing reconcile lock")

        except kopf.TemporaryError:
            raise
        except kopf.PermanentError:
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


@kopf.on.delete('google.dev', 'v1', 'vyosl3vpn')
async def delete_vyosl3vpn(body, spec, name, namespace, logger, **kwargs):
    """Handle VyOSL3VPN deletion — remove VRF/BGP config from all affected routers.

    Reverses exactly what create_vyosl3vpn applied:
      • PE routers: removes the VPN's VRFs from spec.vrfs, clears spec.protocols.bgp,
        and removes the 'vrf' field from any interface that was assigned by this VPN.
      • CE routers: clears spec.protocols.

    Uses the same reconcile lock and router-ready helpers as create so that a
    concurrent VPN creation cannot race against the cleanup patches.
    """
    import json

    logger.info(f"Deleting VyOSL3VPN: {name} — removing router VRF/BGP config")
    await update_status(name, namespace, "VyOSL3VPN", "Deleting", "Deleting VyOSL3VPN")

    # Names of VRFs owned by this VPN, keyed by router name.
    # Used to identify which VRFs to remove and which interfaces to un-assign.
    pe_routers = spec.get('routers', [])
    ce_routers = spec.get('ce_routers', [])

    all_patched_routers = (
        [r['name'] for r in pe_routers] +
        [r['name'] for r in ce_routers]
    )
    if not all_patched_routers:
        logger.info(f"VyOSL3VPN {name}: no routers listed in spec, nothing to clean up")
        return

    logger.info(f"VyOSL3VPN {name}: acquiring reconcile lock for cleanup")
    async with _vpn_reconcile_lock:
        logger.info(f"VyOSL3VPN {name}: reconcile lock acquired")

        try:
            k8s_client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
            router_api = k8s_client.resources.get(api_version='google.dev/v1', kind='VyOSRouter')

            # ── PE router cleanup ────────────────────────────────────────────
            for router_spec in pe_routers:
                router_name = router_spec.get('name')
                if not router_name:
                    continue

                vpn_vrf_names = {v['name'] for v in router_spec.get('vrfs', [])}

                # Collect which interfaces this VPN assigned to a VRF.
                vpn_assigned_interfaces = set()
                for vrf in router_spec.get('vrfs', []):
                    for iface_name in vrf.get('interfaces', []):
                        vpn_assigned_interfaces.add(iface_name)

                try:
                    import json as _json
                    current_router_obj = router_api.get(name=router_name, namespace=namespace)
                    # Convert to plain Python dict (same as create handler) to avoid
                    # ResourceField serialization errors when putting the cleaned list
                    # back into the patch body.
                    _sanitized = kubernetes.client.ApiClient().sanitize_for_serialization(
                        current_router_obj.to_dict()
                    )
                    current_spec = _json.loads(_json.dumps(_sanitized)).get('spec') or {}
                except Exception as e:
                    logger.error(f"Cannot read VyOSRouter {router_name}: {e} — skipping")
                    continue

                # ── Remove VPN-owned VRFs from the current list ──────────────
                current_vrfs = current_spec.get('vrfs') or []
                cleaned_vrfs = [v for v in current_vrfs if v.get('name') not in vpn_vrf_names]
                logger.info(
                    f"VyOSRouter {router_name}: removing VRFs {vpn_vrf_names} "
                    f"({len(current_vrfs)} → {len(cleaned_vrfs)} VRFs remain)"
                )

                # ── Also remove this VPN's BGP VRF entries ───────────────────
                # The Ansible template renders `protocols.bgp.vrfs` separately
                # from `vrfs`.  If BLUE_SPOKE is removed from spec.vrfs but its
                # BGP VRF entry is still in spec.protocols.bgp.vrfs, the template
                # will try to configure BGP for a VRF that was just deleted,
                # causing VyOS to reject the commit and roll back the whole
                # configure session (leaving both VRFs still present).
                current_bgp = (current_spec.get('protocols') or {}).get('bgp') or {}
                current_bgp_vrfs = current_bgp.get('vrfs') or []
                cleaned_bgp_vrfs = [
                    v for v in current_bgp_vrfs
                    if v.get('name') not in vpn_vrf_names
                ]
                cleaned_bgp = dict(current_bgp)
                cleaned_bgp['vrfs'] = cleaned_bgp_vrfs

                logger.info(
                    f"VyOSRouter {router_name}: removing BGP VRFs {vpn_vrf_names} "
                    f"({len(current_bgp_vrfs)} → {len(cleaned_bgp_vrfs)} BGP VRFs remain)"
                )

                # ── Merge-patch: cleaned VRFs + cleaned BGP VRFs ─────────────
                # Global iBGP (AS number, neighbors, route-reflector) is
                # preserved because we carry it forward unchanged in cleaned_bgp.
                # Only the VRF-specific BGP entries added by this VPN are removed.
                router_patch = {
                    'spec': {
                        'vrfs': cleaned_vrfs,
                        'protocols': {
                            'bgp': cleaned_bgp,
                        },
                    }
                }
                try:
                    await patch_vyos_router(router_name, namespace, router_patch)
                    logger.info(f"Patched VyOSRouter {router_name}: removed VPN VRFs {vpn_vrf_names}")
                except Exception as e:
                    logger.error(f"Failed to patch VyOSRouter {router_name}: {e}")
                    continue

                # ── JSON-patch: remove 'vrf' from affected interfaces ─────────
                if vpn_assigned_interfaces:
                    try:
                        # Re-read to get the post-merge-patch interface list.
                        updated_router = router_api.get(name=router_name, namespace=namespace)
                        current_interfaces = updated_router.get('spec', {}).get('interfaces', []) or []

                        json_patch = []
                        for idx, iface in enumerate(current_interfaces):
                            if iface.get('name') in vpn_assigned_interfaces and 'vrf' in iface:
                                json_patch.append({
                                    'op': 'remove',
                                    'path': f'/spec/interfaces/{idx}/vrf',
                                })

                        if json_patch:
                            router_api.patch(
                                name=router_name,
                                namespace=namespace,
                                body=json_patch,
                                content_type='application/json-patch+json',
                            )
                            logger.info(
                                f"VyOSRouter {router_name}: removed 'vrf' field from "
                                f"interfaces {vpn_assigned_interfaces}"
                            )
                    except Exception as e:
                        logger.error(
                            f"Failed to remove interface VRF assignments on "
                            f"{router_name}: {e}"
                        )

            # ── CE router cleanup ────────────────────────────────────────────
            for ce_spec in ce_routers:
                ce_name = ce_spec.get('name')
                if not ce_name:
                    continue
                ce_patch = {'spec': {'protocols': {}}}
                try:
                    await patch_vyos_router(ce_name, namespace, ce_patch)
                    logger.info(f"VyOSRouter {ce_name}: cleared CE protocols config")
                except Exception as e:
                    logger.error(f"Failed to patch CE router {ce_name}: {e}")

            # ── Wait for all routers to finish reconfiguration ───────────────
            if all_patched_routers:
                logger.info(
                    f"VyOSL3VPN {name} delete: waiting for routers to finish "
                    f"reconfiguration: {all_patched_routers}"
                )
                await _wait_for_routers_leave_running(all_patched_routers, namespace, logger)
                await _wait_for_routers_running(all_patched_routers, namespace, logger)

            logger.info(
                f"VyOSL3VPN {name}: VRF/BGP config removed from all routers — "
                f"releasing reconcile lock"
            )

        except kopf.PermanentError:
            # Do not block deletion even on permanent errors — log and continue.
            logger.error(
                f"Permanent error during VyOSL3VPN {name} cleanup; "
                f"router config may need manual cleanup"
            )
        except Exception as e:
            # Same: log but don't raise so the CR is still deleted.
            logger.error(
                f"Unexpected error during VyOSL3VPN {name} cleanup: {e}",
                exc_info=True,
            )
