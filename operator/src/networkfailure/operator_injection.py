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

"""
Operator-mode fault injection for NetworkFailure.

This module implements the 'operator' injectionMode, which injects faults by
patching the VyOS operator CRDs (VyOSInfrastructure, VyOSUnderlay, VyOSL3VPN)
rather than directly modifying the running Docker containers.

Operator mode:
  - Tracks all changes in Spanner via the existing SCD (Slowly Changing Dimension)
    mechanism used by the graph/lifecycle_tasks.py handlers.
  - Changes are visible in GitOps / config management (the CRD is the source of truth).
  - Supports fault types that can be expressed as CRD configuration changes:
      MTU_MISMATCH          → VyOSInfrastructure network mtu field
      BGP_SESSION_DOWN      → VyOSL3VPN bgp.vrfs[*].neighbors list
      PACKET_CORRUPTION     → VyOSUnderlay traffic_policy.network_emulator
      OSPF_AREA_MISMATCH    → VyOSUnderlay router ospf area assignment
      OSPF_COST_INFLATION   → VyOSUnderlay router ospf interface cost
      VRF_RT_MISCONFIGURATION → VyOSL3VPN vrfs[*].rt_import

Direct-only fault types (cannot be expressed in CRDs):
  - TXQUEUE_STARVATION  (Linux kernel txqueuelen — not in VyOS config)
  - LINK_DOWN           (ip link set — not in VyOS config)
  - DUPLICATE_IP        (ip addr add — not in VyOS config)
  - PROCESS_CRASH       (kill/loopback — not in VyOS config)

The operator mode patches the relevant CRD in-place. The original field values
are read from the CRD before patching and stored in original_state for restoration.
When the NetworkFailure is deleted, the CRD is patched back to the original values.
"""

import asyncio
import logging
import copy
import json
from typing import Dict, Any, Optional

import kubernetes

logger = logging.getLogger(__name__)


async def inject_failure_operator(name: str, namespace: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Inject a fault by patching the appropriate VyOS operator CRD.

    Returns:
        dict with 'success' bool, 'original_state' dict, and optionally 'error' string.
    """
    failure_type = spec.get('failureType')
    target = spec.get('target', {})
    parameters = spec.get('parameters', {})

    router = target.get('router')
    interface = target.get('interface', '')
    peer_ip = target.get('peer_ip', '')
    vrf = target.get('vrf', 'global')

    logger.info(f"[operator-mode] Injecting '{failure_type}' on router '{router}' for NetworkFailure '{name}'")

    try:
        if failure_type == 'MTU_MISMATCH':
            return await _inject_mtu_mismatch(name, namespace, router, interface, parameters)

        elif failure_type == 'BGP_SESSION_DOWN':
            return await _inject_bgp_session_down(name, namespace, router, peer_ip, vrf, parameters)

        elif failure_type == 'PACKET_CORRUPTION':
            return await _inject_packet_corruption(name, namespace, router, interface, parameters)

        elif failure_type == 'OSPF_AREA_MISMATCH':
            return await _inject_ospf_area_mismatch(name, namespace, router, interface, parameters)

        elif failure_type == 'OSPF_COST_INFLATION':
            return await _inject_ospf_cost_inflation(name, namespace, router, interface, parameters)

        elif failure_type == 'VRF_RT_MISCONFIGURATION':
            return await _inject_vrf_rt_misconfiguration(name, namespace, router, vrf, parameters)

        else:
            return {
                'success': False,
                'error': f"failureType '{failure_type}' is not supported in operator injection mode. "
                         "Use injectionMode: direct instead."
            }

    except Exception as e:
        logger.error(f"[operator-mode] Unexpected error injecting '{failure_type}': {e}")
        return {'success': False, 'error': str(e)}


async def restore_failure_operator(name: str, namespace: str, spec: Dict[str, Any],
                                    original_state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Restore a fault by patching the VyOS operator CRD back to its original values.

    Returns:
        dict with 'success' bool and optionally 'error' string.
    """
    failure_type = spec.get('failureType')
    target = spec.get('target', {})

    router = target.get('router')
    interface = target.get('interface', '')
    peer_ip = target.get('peer_ip', '')
    vrf = target.get('vrf', 'global')

    logger.info(f"[operator-mode] Restoring '{failure_type}' on router '{router}' for NetworkFailure '{name}'")

    try:
        if failure_type == 'MTU_MISMATCH':
            return await _restore_mtu_mismatch(name, namespace, router, interface, original_state)

        elif failure_type == 'BGP_SESSION_DOWN':
            return await _restore_bgp_session_down(name, namespace, router, peer_ip, vrf, original_state)

        elif failure_type == 'PACKET_CORRUPTION':
            return await _restore_packet_corruption(name, namespace, router, interface, original_state)

        elif failure_type == 'OSPF_AREA_MISMATCH':
            return await _restore_ospf_area_mismatch(name, namespace, router, interface, original_state)

        elif failure_type == 'OSPF_COST_INFLATION':
            return await _restore_ospf_cost_inflation(name, namespace, router, interface, original_state)

        elif failure_type == 'VRF_RT_MISCONFIGURATION':
            parameters = spec.get('parameters', {})
            return await _restore_vrf_rt_misconfiguration(name, namespace, router, vrf, original_state, parameters)

        else:
            return {
                'success': False,
                'error': f"failureType '{failure_type}' is not supported in operator restoration mode."
            }

    except Exception as e:
        logger.error(f"[operator-mode] Unexpected error restoring '{failure_type}': {e}")
        return {'success': False, 'error': str(e)}


# =============================================================================
# MTU_MISMATCH — VyOSInfrastructure network mtu field
# =============================================================================

async def _inject_mtu_mismatch(name, namespace, router, interface, parameters):
    """
    Inject MTU mismatch by patching the VyOSInfrastructure CRD.
    Finds the network connected to router/interface and sets its mtu field.
    """
    mtu = parameters.get('mtu', 1400)
    infra = await _get_crd(namespace, 'vyosinfrastructures', 'google.dev', 'v1')
    if not infra:
        return {'success': False, 'error': 'VyOSInfrastructure CRD not found in namespace'}

    infra_body = infra[0]  # Assume single infra CRD per namespace
    networks = infra_body.get('spec', {}).get('networks', [])

    original_mtu = None
    target_network_name = None

    for net in networks:
        for conn in net.get('connected_routers', []):
            if conn.get('router_name') == router and conn.get('interface') == interface:
                target_network_name = net['name']
                original_mtu = net.get('mtu', 1500)
                break
        if target_network_name:
            break

    if not target_network_name:
        return {
            'success': False,
            'error': f"No network found connecting router '{router}' interface '{interface}' in VyOSInfrastructure"
        }

    # Patch the network mtu in the CRD
    patched_networks = []
    for net in networks:
        net_copy = dict(net)
        if net_copy['name'] == target_network_name:
            net_copy['mtu'] = mtu
        patched_networks.append(net_copy)

    patch = {'spec': {'networks': patched_networks}}
    infra_name = infra_body['metadata']['name']
    success = await _patch_crd(namespace, 'vyosinfrastructures', 'google.dev', 'v1', infra_name, patch)

    if success:
        logger.info(f"[operator-mode] MTU_MISMATCH: patched network '{target_network_name}' mtu={mtu} (was {original_mtu})")
        return {
            'success': True,
            'original_state': {
                'mtu': original_mtu,
                'network_name': target_network_name,
                'infra_name': infra_name,
            }
        }
    return {'success': False, 'error': 'Failed to patch VyOSInfrastructure CRD'}


async def _restore_mtu_mismatch(name, namespace, router, interface, original_state):
    original_mtu = original_state.get('mtu', 1500)
    network_name = original_state.get('network_name')
    infra_name = original_state.get('infra_name')

    if not network_name or not infra_name:
        return {'success': False, 'error': 'original_state missing network_name or infra_name'}

    infra = await _get_crd_by_name(namespace, 'vyosinfrastructures', 'google.dev', 'v1', infra_name)
    if not infra:
        return {'success': False, 'error': f'VyOSInfrastructure {infra_name} not found'}

    networks = infra.get('spec', {}).get('networks', [])
    patched_networks = []
    for net in networks:
        net_copy = dict(net)
        if net_copy['name'] == network_name:
            if original_mtu is None or original_mtu == 1500:
                net_copy.pop('mtu', None)
            else:
                net_copy['mtu'] = original_mtu
        patched_networks.append(net_copy)

    patch = {'spec': {'networks': patched_networks}}
    success = await _patch_crd(namespace, 'vyosinfrastructures', 'google.dev', 'v1', infra_name, patch)
    return {'success': success, 'error': None if success else 'Failed to restore VyOSInfrastructure CRD'}


# =============================================================================
# BGP_SESSION_DOWN — VyOSL3VPN bgp.vrfs[*].neighbors
# =============================================================================

async def _inject_bgp_session_down(name, namespace, router, peer_ip, vrf, parameters):
    """
    Inject BGP session down by clearing the neighbors list in the VyOSL3VPN CRD.
    """
    l3vpn = await _get_crd(namespace, 'vyosl3vpns', 'google.dev', 'v1')
    if not l3vpn:
        return {'success': False, 'error': 'VyOSL3VPN CRD not found in namespace'}

    l3vpn_body = l3vpn[0]
    l3vpn_name = l3vpn_body['metadata']['name']
    routers = l3vpn_body.get('spec', {}).get('routers', [])

    original_neighbors = None
    target_router_idx = None
    target_vrf_idx = None

    for r_idx, r in enumerate(routers):
        if r.get('name') == router:
            bgp_vrfs = r.get('bgp', {}).get('vrfs', [])
            for v_idx, v in enumerate(bgp_vrfs):
                if v.get('name') == vrf:
                    original_neighbors = list(v.get('neighbors', []))
                    target_router_idx = r_idx
                    target_vrf_idx = v_idx
                    break
        if target_router_idx is not None:
            break

    if target_router_idx is None:
        return {
            'success': False,
            'error': f"Router '{router}' VRF '{vrf}' not found in VyOSL3VPN"
        }

    # Patch: clear neighbors list
    patched_routers = copy.deepcopy(routers)
    patched_routers[target_router_idx]['bgp']['vrfs'][target_vrf_idx]['neighbors'] = []

    patch = {'spec': {'routers': patched_routers}}
    success = await _patch_crd(namespace, 'vyosl3vpns', 'google.dev', 'v1', l3vpn_name, patch)

    if success:
        logger.info(f"[operator-mode] BGP_SESSION_DOWN: cleared neighbors for {router}/{vrf} in {l3vpn_name}")
        return {
            'success': True,
            'original_state': {
                'original_neighbors': original_neighbors,
                'l3vpn_name': l3vpn_name,
                'vrf': vrf,
            }
        }
    return {'success': False, 'error': 'Failed to patch VyOSL3VPN CRD'}


async def _restore_bgp_session_down(name, namespace, router, peer_ip, vrf, original_state):
    original_neighbors = original_state.get('original_neighbors', [])
    l3vpn_name = original_state.get('l3vpn_name')

    if not l3vpn_name:
        return {'success': False, 'error': 'original_state missing l3vpn_name'}

    l3vpn = await _get_crd_by_name(namespace, 'vyosl3vpns', 'google.dev', 'v1', l3vpn_name)
    if not l3vpn:
        return {'success': False, 'error': f'VyOSL3VPN {l3vpn_name} not found'}

    routers = copy.deepcopy(l3vpn.get('spec', {}).get('routers', []))
    for r in routers:
        if r.get('name') == router:
            for v in r.get('bgp', {}).get('vrfs', []):
                if v.get('name') == vrf:
                    v['neighbors'] = original_neighbors
                    break
            break

    patch = {'spec': {'routers': routers}}
    success = await _patch_crd(namespace, 'vyosl3vpns', 'google.dev', 'v1', l3vpn_name, patch)
    return {'success': success, 'error': None if success else 'Failed to restore VyOSL3VPN CRD'}


# =============================================================================
# PACKET_CORRUPTION — VyOSUnderlay traffic_policy.network_emulator
# =============================================================================

async def _inject_packet_corruption(name, namespace, router, interface, parameters):
    """
    Inject packet corruption by adding a traffic_policy.network_emulator to the
    VyOSUnderlay CRD for the target router.
    """
    error_rate = parameters.get('error_rate', '5%')
    delay = parameters.get('delay', '10ms')

    underlay = await _get_crd(namespace, 'vyosunderlays', 'google.dev', 'v1')
    if not underlay:
        return {'success': False, 'error': 'VyOSUnderlay CRD not found in namespace'}

    underlay_body = underlay[0]
    underlay_name = underlay_body['metadata']['name']
    routers = underlay_body.get('spec', {}).get('routers', [])

    original_traffic_policy = None
    target_router_idx = None

    for r_idx, r in enumerate(routers):
        if r.get('name') == router:
            original_traffic_policy = copy.deepcopy(r.get('traffic_policy', None))
            target_router_idx = r_idx
            break

    if target_router_idx is None:
        return {'success': False, 'error': f"Router '{router}' not found in VyOSUnderlay"}

    patched_routers = copy.deepcopy(routers)
    patched_routers[target_router_idx]['traffic_policy'] = {
        'network_emulator': [
            {
                'name': f'FAULT_{name.upper().replace("-", "_")}',
                'delay': delay,
                'loss': error_rate,
                'corruption': error_rate,
            }
        ],
        'apply': [
            {'interface': interface, 'out': f'FAULT_{name.upper().replace("-", "_")}'}
        ]
    }

    patch = {'spec': {'routers': patched_routers}}
    success = await _patch_crd(namespace, 'vyosunderlays', 'google.dev', 'v1', underlay_name, patch)

    if success:
        logger.info(f"[operator-mode] PACKET_CORRUPTION: applied traffic_policy to {router}/{interface} in {underlay_name}")
        return {
            'success': True,
            'original_state': {
                'original_traffic_policy': original_traffic_policy,
                'underlay_name': underlay_name,
                'had_existing_qdisc': original_traffic_policy is not None,
            }
        }
    return {'success': False, 'error': 'Failed to patch VyOSUnderlay CRD'}


async def _restore_packet_corruption(name, namespace, router, interface, original_state):
    original_traffic_policy = original_state.get('original_traffic_policy')
    underlay_name = original_state.get('underlay_name')

    if not underlay_name:
        return {'success': False, 'error': 'original_state missing underlay_name'}

    underlay = await _get_crd_by_name(namespace, 'vyosunderlays', 'google.dev', 'v1', underlay_name)
    if not underlay:
        return {'success': False, 'error': f'VyOSUnderlay {underlay_name} not found'}

    routers = copy.deepcopy(underlay.get('spec', {}).get('routers', []))
    for r in routers:
        if r.get('name') == router:
            if original_traffic_policy is None:
                r.pop('traffic_policy', None)
            else:
                r['traffic_policy'] = original_traffic_policy
            break

    patch = {'spec': {'routers': routers}}
    success = await _patch_crd(namespace, 'vyosunderlays', 'google.dev', 'v1', underlay_name, patch)
    return {'success': success, 'error': None if success else 'Failed to restore VyOSUnderlay CRD'}


# =============================================================================
# OSPF_AREA_MISMATCH — VyOSUnderlay router ospf area
# =============================================================================

async def _inject_ospf_area_mismatch(name, namespace, router, interface, parameters):
    """
    Inject OSPF area mismatch by changing the ospf area assignment in VyOSUnderlay.
    Stores the correct area in original_state for restoration.
    """
    wrong_area = parameters.get('wrong_area', '0.0.0.99')
    correct_area = parameters.get('correct_area', '0.0.0.0')

    underlay = await _get_crd(namespace, 'vyosunderlays', 'google.dev', 'v1')
    if not underlay:
        return {'success': False, 'error': 'VyOSUnderlay CRD not found in namespace'}

    underlay_body = underlay[0]
    underlay_name = underlay_body['metadata']['name']
    routers = copy.deepcopy(underlay_body.get('spec', {}).get('routers', []))

    target_router_idx = None
    for r_idx, r in enumerate(routers):
        if r.get('name') == router:
            target_router_idx = r_idx
            break

    if target_router_idx is None:
        return {'success': False, 'error': f"Router '{router}' not found in VyOSUnderlay"}

    # Change the ospf area for the router (interface-level area assignment)
    ospf_config = routers[target_router_idx].get('protocols', {}).get('ospf', {})
    areas = ospf_config.get('areas', [])
    # Replace the backbone area with the wrong area
    patched_areas = []
    for area in areas:
        area_copy = dict(area)
        if area_copy.get('area') == correct_area:
            area_copy['area'] = wrong_area
        patched_areas.append(area_copy)

    if not patched_areas:
        # If no areas defined, add the wrong area
        patched_areas = [{'area': wrong_area, 'type': 'non-backbone'}]

    routers[target_router_idx]['protocols']['ospf']['areas'] = patched_areas

    patch = {'spec': {'routers': routers}}
    success = await _patch_crd(namespace, 'vyosunderlays', 'google.dev', 'v1', underlay_name, patch)

    if success:
        logger.info(f"[operator-mode] OSPF_AREA_MISMATCH: changed area from {correct_area} to {wrong_area} on {router} in {underlay_name}")
        return {
            'success': True,
            'original_state': {
                'correct_area': correct_area,
                'wrong_area': wrong_area,
                'interface': interface,
                'underlay_name': underlay_name,
            }
        }
    return {'success': False, 'error': 'Failed to patch VyOSUnderlay CRD'}


async def _restore_ospf_area_mismatch(name, namespace, router, interface, original_state):
    correct_area = original_state.get('correct_area', '0.0.0.0')
    wrong_area = original_state.get('wrong_area', '0.0.0.99')
    underlay_name = original_state.get('underlay_name')

    if not underlay_name:
        return {'success': False, 'error': 'original_state missing underlay_name'}

    underlay = await _get_crd_by_name(namespace, 'vyosunderlays', 'google.dev', 'v1', underlay_name)
    if not underlay:
        return {'success': False, 'error': f'VyOSUnderlay {underlay_name} not found'}

    routers = copy.deepcopy(underlay.get('spec', {}).get('routers', []))
    for r in routers:
        if r.get('name') == router:
            areas = r.get('protocols', {}).get('ospf', {}).get('areas', [])
            for area in areas:
                if area.get('area') == wrong_area:
                    area['area'] = correct_area
                    area['type'] = 'backbone'
            break

    patch = {'spec': {'routers': routers}}
    success = await _patch_crd(namespace, 'vyosunderlays', 'google.dev', 'v1', underlay_name, patch)
    return {'success': success, 'error': None if success else 'Failed to restore VyOSUnderlay CRD'}


# =============================================================================
# OSPF_COST_INFLATION — VyOSUnderlay router ospf interface cost
# Fault 9: Set OSPF cost to 65535 on a core link. Adjacency stays Full.
# =============================================================================

async def _inject_ospf_cost_inflation(name, namespace, router, interface, parameters):
    """
    Inject OSPF cost inflation by adding an interface_costs entry in VyOSUnderlay.
    The ospf cost is set to 65535 (legal value — no alarms fire).
    """
    ospf_cost = parameters.get('ospf_cost', 65535)

    underlay = await _get_crd(namespace, 'vyosunderlays', 'google.dev', 'v1')
    if not underlay:
        return {'success': False, 'error': 'VyOSUnderlay CRD not found in namespace'}

    underlay_body = underlay[0]
    underlay_name = underlay_body['metadata']['name']
    routers = copy.deepcopy(underlay_body.get('spec', {}).get('routers', []))

    target_router_idx = None
    original_ospf_cost = 1  # OSPF default cost

    for r_idx, r in enumerate(routers):
        if r.get('name') == router:
            target_router_idx = r_idx
            # Check if there's already an interface cost configured
            existing_costs = r.get('protocols', {}).get('ospf', {}).get('interface_costs', [])
            for ic in existing_costs:
                if ic.get('interface') == interface:
                    original_ospf_cost = ic.get('cost', 1)
                    break
            break

    if target_router_idx is None:
        return {'success': False, 'error': f"Router '{router}' not found in VyOSUnderlay"}

    # Add or update interface_costs for this interface
    ospf_config = routers[target_router_idx].setdefault('protocols', {}).setdefault('ospf', {})
    interface_costs = ospf_config.get('interface_costs', [])

    # Remove existing cost for this interface if present
    interface_costs = [ic for ic in interface_costs if ic.get('interface') != interface]
    interface_costs.append({'interface': interface, 'cost': ospf_cost})
    ospf_config['interface_costs'] = interface_costs

    patch = {'spec': {'routers': routers}}
    success = await _patch_crd(namespace, 'vyosunderlays', 'google.dev', 'v1', underlay_name, patch)

    if success:
        logger.info(f"[operator-mode] OSPF_COST_INFLATION: set cost={ospf_cost} on {router}/{interface} in {underlay_name}")
        return {
            'success': True,
            'original_state': {
                'original_ospf_cost': original_ospf_cost,
                'interface': interface,
                'underlay_name': underlay_name,
            }
        }
    return {'success': False, 'error': 'Failed to patch VyOSUnderlay CRD'}


async def _restore_ospf_cost_inflation(name, namespace, router, interface, original_state):
    original_ospf_cost = original_state.get('original_ospf_cost', 1)
    underlay_name = original_state.get('underlay_name')

    if not underlay_name:
        return {'success': False, 'error': 'original_state missing underlay_name'}

    underlay = await _get_crd_by_name(namespace, 'vyosunderlays', 'google.dev', 'v1', underlay_name)
    if not underlay:
        return {'success': False, 'error': f'VyOSUnderlay {underlay_name} not found'}

    routers = copy.deepcopy(underlay.get('spec', {}).get('routers', []))
    for r in routers:
        if r.get('name') == router:
            ospf_config = r.get('protocols', {}).get('ospf', {})
            interface_costs = ospf_config.get('interface_costs', [])
            # Remove the inflated cost entry
            interface_costs = [ic for ic in interface_costs if ic.get('interface') != interface]
            # Restore original cost only if it was non-default
            if original_ospf_cost != 1:
                interface_costs.append({'interface': interface, 'cost': original_ospf_cost})
            ospf_config['interface_costs'] = interface_costs
            break

    patch = {'spec': {'routers': routers}}
    success = await _patch_crd(namespace, 'vyosunderlays', 'google.dev', 'v1', underlay_name, patch)
    return {'success': success, 'error': None if success else 'Failed to restore VyOSUnderlay CRD'}


# =============================================================================
# VRF_RT_MISCONFIGURATION — VyOSL3VPN vrfs[*].rt_import
# Fault 4: Change import RT to non-matching community. Zero alarms.
# =============================================================================

async def _inject_vrf_rt_misconfiguration(name, namespace, router, vrf, parameters):
    """
    Inject VRF RT misconfiguration by changing rt_import in the VyOSL3VPN CRD.
    """
    wrong_rt = parameters.get('wrong_rt', '65035:9999')
    correct_rt = parameters.get('correct_rt', '65035:1030')

    l3vpn = await _get_crd(namespace, 'vyosl3vpns', 'google.dev', 'v1')
    if not l3vpn:
        return {'success': False, 'error': 'VyOSL3VPN CRD not found in namespace'}

    l3vpn_body = l3vpn[0]
    l3vpn_name = l3vpn_body['metadata']['name']
    routers = copy.deepcopy(l3vpn_body.get('spec', {}).get('routers', []))

    original_rt_import = None
    target_router_idx = None
    target_vrf_idx = None

    for r_idx, r in enumerate(routers):
        if r.get('name') == router:
            for v_idx, v in enumerate(r.get('vrfs', [])):
                if v.get('name') == vrf:
                    original_rt_import = list(v.get('rt_import', []))
                    target_router_idx = r_idx
                    target_vrf_idx = v_idx
                    break
        if target_router_idx is not None:
            break

    if target_router_idx is None:
        return {'success': False, 'error': f"Router '{router}' VRF '{vrf}' not found in VyOSL3VPN"}

    # Replace correct_rt with wrong_rt in rt_import list
    new_rt_import = [rt if rt != correct_rt else wrong_rt for rt in original_rt_import]
    if correct_rt not in original_rt_import:
        # If correct_rt wasn't there, just set wrong_rt
        new_rt_import = [wrong_rt]

    routers[target_router_idx]['vrfs'][target_vrf_idx]['rt_import'] = new_rt_import

    patch = {'spec': {'routers': routers}}
    success = await _patch_crd(namespace, 'vyosl3vpns', 'google.dev', 'v1', l3vpn_name, patch)

    if success:
        logger.info(f"[operator-mode] VRF_RT_MISCONFIGURATION: changed rt_import from {original_rt_import} to {new_rt_import} on {router}/{vrf}")
        return {
            'success': True,
            'original_state': {
                'original_rt_import': original_rt_import,
                'correct_rt': correct_rt,
                'wrong_rt': wrong_rt,
                'l3vpn_name': l3vpn_name,
                'vrf': vrf,
            }
        }
    return {'success': False, 'error': 'Failed to patch VyOSL3VPN CRD'}


async def _restore_vrf_rt_misconfiguration(name, namespace, router, vrf, original_state,
                                            parameters: Optional[Dict[str, Any]] = None):
    """
    Restore VRF RT misconfiguration by patching rt_import back to the correct value.

    Resilient to missing original_state (status patch race on fast delete):
    - If l3vpn_name is missing, auto-discovers it by listing all VyOSL3VPN CRDs
      and finding the one containing the target router/VRF.
    - If original_rt_import is missing, reconstructs it from correct_rt in
      original_state or spec parameters.
    """
    if parameters is None:
        parameters = {}

    original_rt_import = original_state.get('original_rt_import', [])
    l3vpn_name = original_state.get('l3vpn_name')

    # Fallback: auto-discover l3vpn_name when original_state is empty due to a
    # status patch race (resource deleted very quickly after creation).
    if not l3vpn_name:
        logger.warning(
            f"[operator-mode] VRF_RT_MISCONFIGURATION restore: original_state missing l3vpn_name "
            f"for '{name}' — auto-discovering VyOSL3VPN CRD containing {router}/{vrf}"
        )
        all_l3vpns = await _get_crd(namespace, 'vyosl3vpns', 'google.dev', 'v1')
        for l3vpn_candidate in all_l3vpns:
            for r in l3vpn_candidate.get('spec', {}).get('routers', []):
                if r.get('name') == router:
                    for v in r.get('vrfs', []):
                        if v.get('name') == vrf:
                            l3vpn_name = l3vpn_candidate['metadata']['name']
                            logger.info(
                                f"[operator-mode] VRF_RT_MISCONFIGURATION restore: "
                                f"auto-discovered l3vpn_name='{l3vpn_name}'"
                            )
                            break
                if l3vpn_name:
                    break
            if l3vpn_name:
                break

    if not l3vpn_name:
        return {
            'success': False,
            'error': (
                f"original_state missing l3vpn_name and no VyOSL3VPN CRD found "
                f"containing router '{router}' VRF '{vrf}' in namespace '{namespace}'"
            )
        }

    # Fallback: reconstruct original_rt_import from correct_rt when missing.
    # correct_rt is saved in original_state by the inject path, and is also
    # available directly from spec.parameters as a last resort.
    if not original_rt_import:
        correct_rt = (
            original_state.get('correct_rt')
            or parameters.get('correct_rt', '')
        )
        if correct_rt:
            original_rt_import = [correct_rt]
            logger.warning(
                f"[operator-mode] VRF_RT_MISCONFIGURATION restore: original_rt_import missing "
                f"for '{name}' — reconstructed from correct_rt='{correct_rt}'"
            )
        else:
            return {
                'success': False,
                'error': (
                    "original_state missing original_rt_import and correct_rt — "
                    "cannot determine what RT to restore. Manual remediation required."
                )
            }

    l3vpn = await _get_crd_by_name(namespace, 'vyosl3vpns', 'google.dev', 'v1', l3vpn_name)
    if not l3vpn:
        return {'success': False, 'error': f'VyOSL3VPN {l3vpn_name} not found'}

    routers = copy.deepcopy(l3vpn.get('spec', {}).get('routers', []))
    for r in routers:
        if r.get('name') == router:
            for v in r.get('vrfs', []):
                if v.get('name') == vrf:
                    v['rt_import'] = original_rt_import
                    break
            break

    patch = {'spec': {'routers': routers}}
    success = await _patch_crd(namespace, 'vyosl3vpns', 'google.dev', 'v1', l3vpn_name, patch)
    if success:
        logger.info(
            f"[operator-mode] VRF_RT_MISCONFIGURATION: restored rt_import={original_rt_import} "
            f"on {router}/{vrf} in {l3vpn_name}"
        )
    return {'success': success, 'error': None if success else 'Failed to restore VyOSL3VPN CRD'}


# =============================================================================
# Kubernetes CRD helpers
# =============================================================================

async def _get_crd(namespace: str, plural: str, group: str, version: str):
    """List all CRDs of the given type in the namespace. Returns list of body dicts."""
    loop = asyncio.get_event_loop()

    def _list():
        try:
            client = kubernetes.client.CustomObjectsApi()
            result = client.list_namespaced_custom_object(
                group=group, version=version, namespace=namespace, plural=plural
            )
            return result.get('items', [])
        except Exception as e:
            logger.error(f"Failed to list {plural} in {namespace}: {e}")
            return []

    return await loop.run_in_executor(None, _list)


async def _get_crd_by_name(namespace: str, plural: str, group: str, version: str, name: str):
    """Get a single CRD by name. Returns body dict or None."""
    loop = asyncio.get_event_loop()

    def _get():
        try:
            client = kubernetes.client.CustomObjectsApi()
            return client.get_namespaced_custom_object(
                group=group, version=version, namespace=namespace, plural=plural, name=name
            )
        except Exception as e:
            logger.error(f"Failed to get {plural}/{name} in {namespace}: {e}")
            return None

    return await loop.run_in_executor(None, _get)


async def _patch_crd(namespace: str, plural: str, group: str, version: str, name: str, patch: dict) -> bool:
    """Patch a CRD using merge-patch. Returns True on success."""
    loop = asyncio.get_event_loop()

    def _patch():
        try:
            client = kubernetes.client.CustomObjectsApi()
            client.patch_namespaced_custom_object(
                group=group, version=version, namespace=namespace,
                plural=plural, name=name, body=patch
            )
            return True
        except Exception as e:
            logger.error(f"Failed to patch {plural}/{name} in {namespace}: {e}")
            return False

    return await loop.run_in_executor(None, _patch)
