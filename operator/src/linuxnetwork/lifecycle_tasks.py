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
# Ansible-based Linux Network Management
#########################################################################

async def create_linux_network(ip_address:str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """Create a Linux network using Ansible"""
    logger.info(f"Creating Linux network: {spec['name']}")

    # Prepare extra variables for Ansible playbook
    extravars = {
        'network_name': spec.get('name'),
        'network_type': spec.get('network_type'),
        'bandwidth': spec.get('bandwidth'),
        'gateway_ip': spec.get('gateway'),
        'operation': 'create'
    }

    result = await _run_ansible_playbook(ip_address, 'create_network.yaml', extravars)
    logger.info(f"Linux network creation result: {result}")

    if result['success']:
        # Capture the default interface
        default_interface = result.get('default_interface', 'unknown')
        interface_ip = result.get('interface_ip', 'unknown')
        default_gateway = result.get('default_gateway', 'unknown')
        return {
            'success': True,
            'default_interface': default_interface,
            'interface_ip': interface_ip,
            'default_gateway': default_gateway
        }
    else:
        return {
            'success': False,
            'error': result.get('error', 'Unknown error during network creation')
        }

async def delete_linux_network(ip_address:str, spec: Dict[str, Any], status: Dict[str, Any]) -> Dict[str, Any]:
    """Delete a Linux network using Ansible"""
    logger.info(f"Deleting Linux network: {spec.get('name')}")
    
    extravars = {
        'network_name': spec.get('name'),
        'network_type': spec.get('network_type'),
    }

    # If network_type is management, pass the interface from status
    if spec.get('network_type') == 'management' and status:
        extravars['parent_interface'] = status.get('interface')
        extravars['interface_ip'] = status.get('interface_ip')
        extravars['default_gateway'] = status.get('gateway')

    result = await _run_ansible_playbook(ip_address, 'delete_network.yaml', extravars)
    
    return {
        'success': result['success'],
        'error': result.get('error') if not result['success'] else None
    }


async def get_detailed_network_status(ip_address: str, network_name: str) -> Dict[str, Any]:
    """Get detailed network status including bridge and veth state"""
    logger.info(f"Getting detailed status for network: {network_name}")
    
    extravars = {
        'network_name': network_name
    }
    
    result = await _run_ansible_playbook(ip_address, 'detailed_status_network.yaml', 
                                         extravars, is_monitoring=True)
    
    # Extract detailed bridge state from ansible_facts
    return {
        'exists': result.get('bridge_exists', False),
        'operational_state': result.get('bridge_state', 'unknown'),
        'mtu': result.get('bridge_mtu', 1500),
        'mac_address': result.get('bridge_mac', ''),
        'ip_address': result.get('bridge_ip', ''),
        'metrics': result.get('bridge_metrics', {}),
        'veth_pairs': result.get('veth_pairs', []),
        'error': result.get('error') if not result.get('success', True) else None
    }

#########################################################################
# Ansible Execution Helper
#########################################################################
    
async def _run_ansible_playbook(ip_address:str, playbook: str, extravars: Dict[str, Any], is_monitoring: bool = False) -> Dict[str, Any]:
    """Run an Ansible playbook with the given extra variables"""
    
    # Get the appropriate Ansible semaphore for throttling
    from utils.ansible import get_ansible_operational_semaphore, get_ansible_monitor_semaphore
    semaphore = get_ansible_monitor_semaphore() if is_monitoring else get_ansible_operational_semaphore()
    
    # Prepare host inventory
    hosts = {
        'hosts': {
            "monitor": {
                'ansible_host': ip_address,
                'ansible_user': os.getenv("GOOGLE_VM_USER"),
                'ansible_connection': 'ssh',
                'ansible_ssh_private_key_file': constants.basedir+'/google-compute',
                'ansible_ssh_common_args': '-o StrictHostKeyChecking=no'
            }
        }
    }
    
    logger.info(f"Running Ansible playbook: {playbook}")
    logger.info(f"Extra vars: {extravars}")
    
    # Hard timeout (seconds) for any single Ansible playbook run.
    # Prevents a hung SSH connection or stalled command from keeping
    # a resource permanently stuck in "Creating".
    ANSIBLE_TIMEOUT_SECONDS = 600

    def run_ansible(temp_dir):
        """Wrapper function to run ansible_runner.run_async"""
        try:
            thread, runner = ansible_runner.run_async(
                private_data_dir=temp_dir,
                project_dir=constants.basedir + "/linuxnetwork/playbooks",
                inventory={'all': hosts},
                playbook=playbook,
                extravars=extravars,
                quiet=False,
                verbosity=1
            )
            # Wait for the thread to complete, but don't block forever.
            # If Ansible hangs (e.g. SSH stall, tc deadlock) the thread.join()
            # without a timeout would park the resource in "Creating" indefinitely.
            thread.join(timeout=ANSIBLE_TIMEOUT_SECONDS)
            if thread.is_alive():
                logger.error(
                    f"Ansible playbook '{playbook}' did not finish within "
                    f"{ANSIBLE_TIMEOUT_SECONDS}s — treating as failure"
                )
                return None
            return runner
        except Exception as e:
            logger.error(f"Ansible execution failed: {e}")
            return None
    
    # Throttle concurrent Ansible executions using semaphore
    async with semaphore:
        logger.info(f"Acquired Ansible semaphore for playbook: {playbook}")
        import tempfile
        import shutil
        temp_dir = tempfile.mkdtemp(prefix="ansible_network_")
        try:
            # Execute in thread pool to avoid blocking the async event loop
            loop = asyncio.get_event_loop()
            runner = await loop.run_in_executor(None, run_ansible, temp_dir)
            
            if runner is None:
                return {
                    'success': False,
                    'error': 'Failed to execute Ansible playbook'
                }
            
            if runner.status == 'successful':
                # Extract results from Ansible facts if available
                result_data = {}
                
                # Try to get results from all events
                for event in runner.events:
                    if event.get('event') == 'runner_on_ok':
                        event_data = event.get('event_data', {})
                        res = event_data.get('res', {})
                        
                        # Extract ansible_facts if they exist
                        if 'ansible_facts' in res:
                            ansible_facts = res['ansible_facts']
                            
                            # Extract default_interface if present
                            if 'default_interface' in ansible_facts:
                                result_data['default_interface'] = ansible_facts['default_interface']
                                logger.debug(f"Captured default_interface: {ansible_facts['default_interface']}")
                            
                            # Extract default_gateway if present
                            if 'default_gateway' in ansible_facts:
                                result_data['default_gateway'] = ansible_facts['default_gateway']
                                logger.debug(f"Captured default_gateway: {ansible_facts['default_gateway']}")

                            # Extract interface_ip if present
                            if 'interface_ip' in ansible_facts:
                                result_data['interface_ip'] = ansible_facts['interface_ip']
                                logger.debug(f"Captured interface_ip: {ansible_facts['interface_ip']}")
                            
                            # Extract exists fact (for status check)
                            if 'exists' in ansible_facts:
                                result_data['exists'] = ansible_facts['exists']
                                logger.debug(f"Captured exists: {ansible_facts['exists']}")
                            
                            # Extract network_info (for status check)
                            if 'network_info' in ansible_facts:
                                result_data['network_info'] = ansible_facts['network_info']
                                logger.debug(f"Captured network_info: {ansible_facts['network_info']}")
                            
                            # Extract detailed bridge status (for detailed_status_network.yaml)
                            if 'bridge_exists' in ansible_facts:
                                result_data['bridge_exists'] = ansible_facts['bridge_exists']
                                logger.debug(f"Captured bridge_exists: {ansible_facts['bridge_exists']}")
                            
                            if 'bridge_state' in ansible_facts:
                                result_data['bridge_state'] = ansible_facts['bridge_state']
                                logger.debug(f"Captured bridge_state: {ansible_facts['bridge_state']}")
                            
                            if 'bridge_mtu' in ansible_facts:
                                result_data['bridge_mtu'] = ansible_facts['bridge_mtu']
                                logger.debug(f"Captured bridge_mtu: {ansible_facts['bridge_mtu']}")
                            
                            if 'bridge_mac' in ansible_facts:
                                result_data['bridge_mac'] = ansible_facts['bridge_mac']
                                logger.debug(f"Captured bridge_mac: {ansible_facts['bridge_mac']}")
                            
                            if 'bridge_ip' in ansible_facts:
                                result_data['bridge_ip'] = ansible_facts['bridge_ip']
                                logger.debug(f"Captured bridge_ip: {ansible_facts['bridge_ip']}")
                            
                            if 'bridge_metrics' in ansible_facts:
                                result_data['bridge_metrics'] = ansible_facts['bridge_metrics']
                                logger.debug(f"Captured bridge_metrics: {ansible_facts['bridge_metrics']}")
                            
                            if 'veth_pairs' in ansible_facts:
                                veth_pairs_raw = ansible_facts['veth_pairs']
                                if isinstance(veth_pairs_raw, list):
                                    result_data['veth_pairs'] = veth_pairs_raw
                                else:
                                    logger.warning(f"veth_pairs Ansible fact is not a list (got {type(veth_pairs_raw).__name__}), ignoring")
                                    result_data['veth_pairs'] = []
                                logger.debug(f"Captured veth_pairs: {result_data['veth_pairs']}")

                logger.info(f"Final extracted data: {result_data}")
                return {
                    'success': True,
                    **result_data
                }
            else:
                # Extract error information — check unreachable first (transient), then task failures
                error_msg = f"Ansible playbook failed with status: {runner.status}"

                all_events = list(runner.events)
                logger.error(f"Ansible playbook '{playbook}' failed (status={runner.status}, rc={runner.rc}). "
                             f"Total events: {len(all_events)}")

                # Log every event type so we can see what's happening
                for event in all_events:
                    event_type = event.get('event', '')
                    event_data = event.get('event_data', {})
                    logger.error(f"  event={event_type} task={event_data.get('task','')!r} "
                                 f"res_keys={list(event_data.get('res', {}).keys())}")

                for event in all_events:
                    event_type = event.get('event', '')
                    event_data = event.get('event_data', {})
                    res = event_data.get('res', {})

                    if event_type == 'runner_on_unreachable':
                        # SSH/network connectivity failure — always transient
                        detail = res.get('msg', event_data.get('task', 'unknown host'))
                        error_msg = f"Host unreachable: {detail}"
                        break

                    if event_type == 'runner_on_failed':
                        task_name = event_data.get('task', '')
                        msg = res.get('msg', '')
                        stderr = res.get('stderr', '')
                        stdout = res.get('stdout', '')
                        rc = res.get('rc', None)

                        # Prefer the most informative field
                        raw_detail = msg or stderr or stdout or str(res)

                        # Annotate timeout kills (exit code 124 from timeout(1) wrapper)
                        if rc == 124 or 'Killed' in raw_detail or 'exit code 124' in raw_detail:
                            error_msg = f"exit code 124: command timed out in task '{task_name}'"
                        else:
                            error_msg = f"Task '{task_name}' failed: {raw_detail}" if task_name else raw_detail
                        break

                # Fallback: try runner stdout if still generic
                if error_msg.startswith("Ansible playbook failed with status:"):
                    try:
                        stdout_lines = list(runner.stdout)
                        if stdout_lines:
                            stdout_tail = ''.join(stdout_lines[-20:]).strip()
                            if stdout_tail:
                                error_msg = f"Ansible stdout: {stdout_tail[-800:]}"
                    except Exception:
                        pass

                logger.error(f"Ansible playbook execution failed: {error_msg}")
                return {
                    'success': False,
                    'error': error_msg
                }
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
