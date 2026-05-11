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
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from networkfailure.lifecycle_tasks import inject_failure, restore_failure
from utils.compute import get_ip

logger = logging.getLogger(__name__)

#########################################################################
# NetworkFailure Lifecycle Handlers
#########################################################################

@kopf.on.create('google.dev', 'v1', 'networkfailure')
async def create_network_failure_handler(body, spec, name, namespace, uid, logger, **kwargs):
    """
    Handle NetworkFailure creation by injecting the specified fault into the
    running VyOS router topology.

    Workflow:
    1. Validate spec (required fields per failure type)
    2. Retrieve Network VM IP for Ansible execution
    3. Set status to Injecting
    4. Run the injection Ansible playbook via lifecycle_tasks
    5. Store the returned original_state in status for later restoration
    6. Set status to Active with timestamp
    """
    failure_type = spec.get('failureType')
    target = spec.get('target', {})
    parameters = spec.get('parameters', {})
    router = target.get('router')

    logger.info(f"Creating NetworkFailure '{name}': type={failure_type}, router={router}")

    # --- Validate required fields per failure type ---
    validation_error = _validate_spec(failure_type, target, parameters)
    if validation_error:
        await _update_status(name, namespace, "Failed", validation_error)
        raise kopf.PermanentError(validation_error)

    # --- Get Network VM IP address ---
    ip_address = await get_ip("automation", "networkvm")
    if ip_address is None:
        raise kopf.TemporaryError(
            "No IP address found on Network VM yet — waiting for networkvm to be ready",
            delay=15
        )
    logger.info(f"Network VM address: {ip_address}")

    # --- Mark as Injecting ---
    await _update_status(name, namespace, "Injecting",
                         f"Injecting {failure_type} on router '{router}'")

    # --- Run injection ---
    try:
        result = await inject_failure(name, ip_address, spec)
    except Exception as e:
        error_msg = f"Unexpected error during fault injection: {e}"
        logger.error(error_msg)
        await _update_status(name, namespace, "Failed", error_msg)
        raise kopf.PermanentError(error_msg)

    if not result['success']:
        error_msg = f"Fault injection failed: {result.get('error', 'unknown error')}"
        logger.error(error_msg)
        await _update_status(name, namespace, "Failed", error_msg)
        raise kopf.PermanentError(error_msg)

    # --- Mark as Active and store original state ---
    injected_at = datetime.now(timezone.utc).isoformat()
    original_state = result.get('original_state', {})

    await _update_status(
        name, namespace, "Active",
        f"Fault '{failure_type}' is active on router '{router}' — original state saved for restoration",
        additional_data={
            'injected_at': injected_at,
            'original_state': original_state,
        }
    )
    logger.info(f"NetworkFailure '{name}' successfully injected. original_state={original_state}")


@kopf.on.delete('google.dev', 'v1', 'networkfailure')
async def delete_network_failure_handler(body, spec, name, namespace, logger, **kwargs):
    """
    Handle NetworkFailure deletion by restoring the original network configuration.

    Workflow:
    1. Retrieve the original_state saved during injection from the resource status
    2. Retrieve Network VM IP for Ansible execution
    3. Set status to Restoring
    4. Run the restoration Ansible playbook via lifecycle_tasks
    5. Set status to Restored
    """
    failure_type = spec.get('failureType')
    target = spec.get('target', {})
    router = target.get('router')
    status = body.get('status', {})
    original_state = status.get('original_state', {})

    logger.info(f"Deleting NetworkFailure '{name}': type={failure_type}, router={router}")

    # --- Get Network VM IP address ---
    ip_address = await get_ip("automation", "networkvm")
    if ip_address is None:
        logger.warning(
            f"No IP address found on Network VM, skipping restoration for '{name}'. "
            "The fault may remain active on the router."
        )
        return

    # --- Mark as Restoring ---
    await _update_status(name, namespace, "Restoring",
                         f"Restoring original configuration on router '{router}'")

    # --- Run restoration ---
    try:
        result = await restore_failure(name, ip_address, spec, original_state)
    except Exception as e:
        error_msg = f"Unexpected error during fault restoration: {e}"
        logger.error(error_msg)
        await _update_status(name, namespace, "Failed", error_msg)
        # Do NOT raise a PermanentError on delete — allow the resource to be removed
        # even if restoration fails. Log the issue for manual remediation.
        logger.warning(
            f"NetworkFailure '{name}' deletion will proceed despite restoration failure. "
            "Manual remediation may be required on the router."
        )
        return

    if not result['success']:
        logger.warning(
            f"Restoration playbook reported failure for NetworkFailure '{name}': "
            f"{result.get('error', 'unknown error')}. "
            "Proceeding with resource deletion. Manual remediation may be required."
        )
    else:
        restored_at = datetime.now(timezone.utc).isoformat()
        await _update_status(
            name, namespace, "Restored",
            f"Original configuration restored on router '{router}'",
            additional_data={'restored_at': restored_at}
        )
        logger.info(f"NetworkFailure '{name}' successfully restored at {restored_at}")


@kopf.on.resume('google.dev', 'v1', 'networkfailure')
async def resume_network_failure_handler(body, spec, name, namespace, logger, **kwargs):
    """
    Re-sync NetworkFailure state when the operator restarts.

    If a failure was Active when the operator restarted, log a warning so that
    operators are aware that a fault is still injected in the network.
    No re-injection is performed on resume — the fault persists in the network
    and the resource continues tracking it.
    """
    failure_type = spec.get('failureType')
    target = spec.get('target', {})
    router = target.get('router')
    status = body.get('status', {})
    phase = status.get('phase', 'Unknown')

    logger.info(
        f"Resuming NetworkFailure '{name}': type={failure_type}, router={router}, phase={phase}"
    )

    if phase == 'Active':
        injected_at = status.get('injected_at', 'unknown time')
        logger.warning(
            f"NetworkFailure '{name}' ({failure_type} on {router}) is still ACTIVE "
            f"(injected at {injected_at}). The fault is currently affecting the network. "
            "Delete this resource to restore the original configuration."
        )
    elif phase == 'Injecting':
        # The operator crashed mid-injection — mark as Failed for visibility
        logger.error(
            f"NetworkFailure '{name}' was in 'Injecting' phase when the operator restarted. "
            "The fault state is uncertain. Manual verification is required."
        )
        await _update_status(
            name, namespace, "Failed",
            "Operator restarted during fault injection. Fault state is uncertain. "
            "Delete this resource and manually verify the router configuration."
        )
    elif phase == 'Restoring':
        logger.error(
            f"NetworkFailure '{name}' was in 'Restoring' phase when the operator restarted. "
            "The restoration state is uncertain. Manual verification is required."
        )
        await _update_status(
            name, namespace, "Failed",
            "Operator restarted during fault restoration. Restoration state is uncertain. "
            "Delete this resource and manually verify the router configuration."
        )


#########################################################################
# Input Validation
#########################################################################

def _validate_spec(failure_type: str, target: dict, parameters: dict) -> Optional[str]:
    """
    Validate that all required fields are present for the given failure type.
    Returns an error message string if validation fails, or None if valid.
    """
    router = target.get('router')
    interface = target.get('interface')
    peer_ip = target.get('peer_ip')
    vrf = target.get('vrf')

    if not router:
        return "spec.target.router is required for all failure types"

    if failure_type == 'MTU_MISMATCH':
        if not interface:
            return "spec.target.interface is required for MTU_MISMATCH"
        mtu = parameters.get('mtu')
        if mtu is None:
            return "spec.parameters.mtu is required for MTU_MISMATCH"

    elif failure_type == 'BGP_SESSION_DOWN':
        if not peer_ip:
            return "spec.target.peer_ip is required for BGP_SESSION_DOWN"
        if not vrf:
            return "spec.target.vrf is required for BGP_SESSION_DOWN"
        if parameters.get('remote_as') is None:
            return "spec.parameters.remote_as is required for BGP_SESSION_DOWN"

    elif failure_type == 'PROCESS_CRASH':
        method = parameters.get('method', 'loopback_disable')
        if method not in ('process_kill', 'loopback_disable'):
            return "spec.parameters.method must be 'process_kill' or 'loopback_disable'"

    elif failure_type == 'PACKET_CORRUPTION':
        if not interface:
            return "spec.target.interface is required for PACKET_CORRUPTION"
        error_rate = parameters.get('error_rate', '5%')
        if not error_rate:
            return "spec.parameters.error_rate is required for PACKET_CORRUPTION"

    elif failure_type == 'LINK_DOWN':
        if not interface:
            return "spec.target.interface is required for LINK_DOWN"

    elif failure_type == 'OSPF_AREA_MISMATCH':
        if not interface:
            return "spec.target.interface is required for OSPF_AREA_MISMATCH"
        if not parameters.get('wrong_area'):
            return "spec.parameters.wrong_area is required for OSPF_AREA_MISMATCH"
        if not parameters.get('correct_area'):
            return "spec.parameters.correct_area is required for OSPF_AREA_MISMATCH"

    elif failure_type == 'DUPLICATE_IP':
        if not interface:
            return "spec.target.interface is required for DUPLICATE_IP"
        if not parameters.get('duplicate_ip'):
            return "spec.parameters.duplicate_ip is required for DUPLICATE_IP"

    else:
        return f"Unknown failureType: {failure_type}"

    return None


#########################################################################
# Status Management
#########################################################################

async def _update_status(name: str, namespace: str, phase: str, message: str,
                          additional_data: Optional[Dict[str, Any]] = None):
    """Update the status subresource of a NetworkFailure resource."""
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='NetworkFailure')

    try:
        resource = api.get(name=name, namespace=namespace)
        resource_dict = resource.to_dict()

        if 'status' not in resource_dict or resource_dict['status'] is None:
            resource_dict['status'] = {}

        status = {
            'phase': phase,
            'message': message,
        }

        if additional_data:
            status.update(additional_data)

        resource_dict['status'].update(status)

        api.patch(
            namespace=namespace,
            name=name,
            body=resource_dict,
            content_type='application/merge-patch+json',
            subresource='status'
        )

        logger.debug(f"Updated status for NetworkFailure '{name}': {phase} — {message}")

    except kubernetes.client.rest.ApiException as e:
        if e.status == 422 and "status" in str(e):
            logger.warning(
                f"Status subresource not enabled for NetworkFailure '{name}', "
                "skipping status update."
            )
        else:
            logger.error(f"Failed to update status for NetworkFailure '{name}': {e}")
    except Exception as e:
        logger.error(f"Unexpected error updating status for NetworkFailure '{name}': {e}")
