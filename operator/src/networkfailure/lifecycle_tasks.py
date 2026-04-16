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

import asyncio
import ansible_runner
import os
import utils.constants as constants
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

#########################################################################
# NetworkFailure Ansible-based Task Execution
#########################################################################

async def inject_failure(name: str, networkvm_ip_address: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Inject a network fault by running the failure_inject Ansible playbook on the network VM.

    The playbook uses 'docker exec' to interact directly with the VyOS router containers.
    Before applying the fault, the playbook reads and returns the current (original)
    configuration so it can be saved in status.original_state for later restoration.

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

    # Apply defaults for optional parameters
    mtu = parameters.get('mtu', 1400)
    error_rate = parameters.get('error_rate', '5%')
    method = parameters.get('method', 'loopback_disable')
    remote_as = parameters.get('remote_as', 0)
    wrong_area = parameters.get('wrong_area', '')
    correct_area = parameters.get('correct_area', '0.0.0.0')
    duplicate_ip = parameters.get('duplicate_ip', '')

    extravars = {
        'operation': 'inject',
        'failure_name': name,
        'failure_type': failure_type,
        'router': router,
        'interface': interface,
        'peer_ip': peer_ip,
        'vrf': vrf,
        'mtu': mtu,
        'error_rate': error_rate,
        'method': method,
        'remote_as': remote_as,
        'wrong_area': wrong_area,
        'correct_area': correct_area,
        'duplicate_ip': duplicate_ip,
    }

    logger.info(f"Injecting fault '{failure_type}' on router '{router}' for NetworkFailure '{name}'")
    logger.info(f"Injection extravars: {extravars}")

    result = await _run_ansible_playbook(networkvm_ip_address, 'failure_inject.yaml', extravars)

    if result['success']:
        original_state = result.get('original_state', {})
        logger.info(f"Fault injection successful for '{name}'. original_state={original_state}")
        return {
            'success': True,
            'original_state': original_state,
        }
    else:
        logger.error(f"Fault injection failed for '{name}': {result.get('error')}")
        return {
            'success': False,
            'error': result.get('error', 'Ansible playbook failed'),
        }


async def restore_failure(name: str, networkvm_ip_address: str, spec: Dict[str, Any],
                          original_state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Restore the original network configuration by running the failure_restore Ansible playbook.

    Uses the original_state dict saved during injection to know exactly what to revert.

    Returns:
        dict with 'success' bool and optionally 'error' string.
    """
    failure_type = spec.get('failureType')
    target = spec.get('target', {})
    parameters = spec.get('parameters', {})

    router = target.get('router')
    interface = target.get('interface', '')
    peer_ip = target.get('peer_ip', '')
    vrf = target.get('vrf', 'global')

    # Merge spec parameters with original_state — original_state takes precedence
    # for values that were read from the router before the fault was applied.
    method = parameters.get('method', original_state.get('method', 'loopback_disable'))
    original_mtu = original_state.get('mtu', 1500)
    original_remote_as = original_state.get('remote_as', parameters.get('remote_as', 0))
    original_correct_area = original_state.get('correct_area',
                                               parameters.get('correct_area', '0.0.0.0'))
    wrong_area = original_state.get('wrong_area', parameters.get('wrong_area', ''))
    duplicate_ip = original_state.get('duplicate_ip', parameters.get('duplicate_ip', ''))
    had_existing_qdisc = original_state.get('had_existing_qdisc', False)

    extravars = {
        'operation': 'restore',
        'failure_name': name,
        'failure_type': failure_type,
        'router': router,
        'interface': interface,
        'peer_ip': peer_ip,
        'vrf': vrf,
        'method': method,
        # Restoration-specific values from original_state
        'original_mtu': original_mtu,
        'original_remote_as': original_remote_as,
        'correct_area': original_correct_area,
        'wrong_area': wrong_area,
        'duplicate_ip': duplicate_ip,
        'had_existing_qdisc': had_existing_qdisc,
    }

    logger.info(f"Restoring fault '{failure_type}' on router '{router}' for NetworkFailure '{name}'")
    logger.info(f"Restoration extravars: {extravars}")

    result = await _run_ansible_playbook(networkvm_ip_address, 'failure_restore.yaml', extravars)

    if result['success']:
        logger.info(f"Fault restoration successful for NetworkFailure '{name}'")
        return {'success': True}
    else:
        logger.error(f"Fault restoration failed for '{name}': {result.get('error')}")
        return {
            'success': False,
            'error': result.get('error', 'Ansible playbook failed'),
        }


#########################################################################
# Ansible Execution Helper
#########################################################################

async def _run_ansible_playbook(networkvm_ip_address: str, playbook: str,
                                extravars: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run an Ansible playbook on the network VM with the given extra variables.

    The playbook is executed inside the networkfailure/playbooks directory.
    Uses the shared Ansible semaphore to throttle concurrent executions.
    """
    from utils.ansible import get_ansible_semaphore
    semaphore = get_ansible_semaphore()

    hosts = {
        'hosts': {
            'networkvm': {
                'ansible_host': networkvm_ip_address,
                'ansible_user': os.getenv("GOOGLE_VM_USER"),
                'ansible_connection': 'ssh',
                'ansible_ssh_private_key_file': constants.basedir + '/google-compute',
                'ansible_ssh_common_args': '-o StrictHostKeyChecking=no',
            }
        }
    }

    logger.info(f"Running Ansible playbook: {playbook} on {networkvm_ip_address}")

    def run_ansible():
        try:
            thread, runner = ansible_runner.run_async(
                private_data_dir=constants.basedir + '/networkfailure/playbooks',
                inventory={'all': hosts},
                playbook=playbook,
                extravars=extravars,
                quiet=False,
                verbosity=1,
            )
            thread.join()
            return runner
        except Exception as e:
            logger.error(f"Ansible execution error: {e}")
            return None

    async with semaphore:
        logger.info(f"Acquired Ansible semaphore for playbook: {playbook}")
        loop = asyncio.get_event_loop()
        runner = await loop.run_in_executor(None, run_ansible)

        if runner is None:
            return {
                'success': False,
                'error': 'Failed to launch Ansible playbook — runner returned None',
            }

        if runner.status == 'successful':
            result_data = {}

            # Extract custom return data set by the playbook via set_fact / debug
            for event in runner.events:
                if event.get('event') == 'runner_on_ok':
                    event_data = event.get('event_data', {})
                    res = event_data.get('res', {})

                    # The playbook sets ansible_facts.original_state via set_fact
                    ansible_facts = res.get('ansible_facts', {})
                    if 'original_state' in ansible_facts:
                        result_data['original_state'] = ansible_facts['original_state']

                    # Direct result keys
                    if 'original_state' in res:
                        result_data['original_state'] = res['original_state']
                    if 'message' in res:
                        result_data['message'] = res['message']

            return {
                'success': True,
                **result_data,
            }
        else:
            error_msg = f"Ansible playbook '{playbook}' failed with status: {runner.status}"
            for event in runner.events:
                if event.get('event') == 'runner_on_failed':
                    event_data = event.get('event_data', {})
                    res = event_data.get('res', {})
                    if 'msg' in res:
                        error_msg = res['msg']
                    elif 'stderr' in res:
                        error_msg = res['stderr']
                    break

            logger.error(f"Ansible playbook execution failed: {error_msg}")
            return {
                'success': False,
                'error': error_msg,
            }
