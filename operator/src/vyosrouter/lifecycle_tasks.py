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
import json
import utils.constants as constants
import logging
import kopf
from typing import Dict, Any
import kubernetes

logger = logging.getLogger(__name__)

#########################################################################
# Ansible-based VyOS Router Management
#########################################################################

async def create_vyos_router(ip_address:str, router_config: Dict[str, Any]) -> Dict[str, Any]:
    """Create a VyOS router container using Ansible"""
    logger.info(f"Creating VyOS router: {router_config['name']}")
    
    # Prepare extra variables for Ansible playbook
    extravars = {
        'router_name': router_config['name'],
        'router_hostname': router_config['hostname'],
        'router_id': router_config['router_id'],
        'vyos_image': router_config.get('image', 'vyos:1.5'),
        'source_network': router_config.get('source_network'),
        'interfaces': router_config.get('interfaces'),
        'operation': 'create'
    }
    
    result = await _run_ansible_playbook(ip_address, 'router_management.yaml', extravars)
    
    if result['success']:
        return {
            'success': True,
            'container_id': result.get('container_id'),
            'management_ip': result.get('management_ip'),
            'applied_config': result.get('applied_config')
        }
    else:
        return {
            'success': False,
            'error': result.get('error', 'Unknown error during router creation')
        }

async def configure_vyos_router(ip_address:str,router_config: Dict[str, Any]) -> Dict[str, Any]:
    """Configure a VyOS router using Ansible"""
    logger.info(f"Configuring VyOS router: {router_config['name']}")
    
    # Prepare configuration data
    extravars = {
        'router_name': router_config['name'],
        'router_hostname': router_config['hostname'],
        'router_id': router_config['router_id'],
        'interfaces': router_config.get('interfaces', []),
        'vrfs': router_config.get('vrfs', []),
        'protocols': router_config.get('protocols', {}),
        'services': router_config.get('services', {}),
        'qos_policies': router_config.get('qos', {}).get('policies', []),
        'firewall_policies': router_config.get('firewall', {}).get('policies', []),
        'influxdb_url': os.getenv('INFLUXDB_URL'),
        'influxdb_port': os.getenv('INFLUXDB_PORT'),
        'influxdb_token': os.getenv('INFLUXDB_TOKEN'),
        'operation': 'configure'
    }
    
    result = await _run_ansible_playbook(ip_address, 'router_configuration.yaml', extravars)
    
    return {
        'success': result['success'],
        'error': result.get('error') if not result['success'] else None,
        'applied_config': result.get('applied_config')
    }

async def update_vyos_router(ip_address:str, router_config: Dict[str, Any]) -> Dict[str, Any]:
    """Update a VyOS router configuration using Ansible"""
    logger.info(f"Updating VyOS router: {router_config['name']}")
    
    # Prepare updated configuration data
    extravars = {
        'router_name': router_config['name'],
        'router_hostname': router_config['hostname'],
        'router_id': router_config['router_id'],
        'interfaces': router_config.get('interfaces', []),
        'vrfs': router_config.get('vrfs', []),
        'protocols': router_config.get('protocols', {}),
        'services': router_config.get('services', {}),
        'qos_policies': router_config.get('qos', {}).get('policies', []),
        'firewall_policies': router_config.get('firewall', {}).get('policies', []),
        'influxdb_url': os.getenv('INFLUXDB_URL'),
        'influxdb_port': os.getenv('INFLUXDB_PORT'),
        'influxdb_token': os.getenv('INFLUXDB_TOKEN'),
        'operation': 'update'
    }
    
    result = await _run_ansible_playbook(ip_address, 'router_configuration.yaml', extravars)
    
    return {
        'success': result['success'],
        'error': result.get('error') if not result['success'] else None,
        'applied_config': result.get('applied_config')
    }

async def delete_vyos_router(ip_address:str, router_name: str, interfaces: list = None) -> Dict[str, Any]:
    """Delete a VyOS router container using Ansible"""
    logger.info(f"Deleting VyOS router: {router_name}")
    
    extravars = {
        'router_name': router_name,
        'interfaces': interfaces or [],
        'operation': 'delete'
    }
    
    result = await _run_ansible_playbook(ip_address, 'router_management.yaml', extravars)
    
    return {
        'success': result['success'],
        'error': result.get('error') if not result['success'] else None
    }

async def get_vyos_router_status(ip_address:str, router_name: str) -> Dict[str, Any]:
    """Get VyOS router status using Ansible"""
    logger.debug(f"Getting VyOS router status: {router_name}")
    
    extravars = {
        'router_name': router_name,
        'operation': 'status'
    }
    
    result = await _run_ansible_playbook(ip_address, 'router_management.yaml', extravars)
    
    return result

#########################################################################
# Ansible Execution Helper
#########################################################################

async def _run_ansible_playbook(ip_address:str, playbook: str, extravars: Dict[str, Any]) -> Dict[str, Any]:
    """Run an Ansible playbook with the given extra variables"""
    
    # Get the Ansible semaphore for throttling
    from utils.ansible import get_ansible_semaphore
    semaphore = get_ansible_semaphore()
    
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
    
    def run_ansible(temp_dir):
        """Wrapper function to run ansible_runner.run_async"""
        try:
            thread, runner = ansible_runner.run_async(
                private_data_dir=temp_dir,
                project_dir=constants.basedir + "/vyosrouter/playbooks",
                inventory={'all': hosts},
                playbook=playbook,
                extravars=extravars,
                quiet=False,
                verbosity=1
            )
            # Wait for the thread to complete
            thread.join()
            return runner
        except Exception as e:
            logger.error(f"Ansible execution failed: {e}")
            return None
    
    # Throttle concurrent Ansible executions using semaphore
    async with semaphore:
        logger.info(f"Acquired Ansible semaphore for playbook: {playbook}")
        import tempfile
        import shutil
        temp_dir = tempfile.mkdtemp(prefix="ansible_vyosrouter_")
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
                
                # Try to get results from the last event
                for event in runner.events:
                    if event.get('event') == 'runner_on_ok':
                        event_data = event.get('event_data', {})
                        res = event_data.get('res', {})
                        task_name = event_data.get('task', '')
                        
                        # Extract router information from Ansible results
                        if task_name == "Capture VyOS configuration":
                            result_data['applied_config'] = res.get('vyos_config_commands', '')
                            
                        if 'container_id' in res:
                            result_data['container_id'] = res['container_id']
                        if 'management_ip' in res:
                            result_data['management_ip'] = res['management_ip']
                        if 'running' in res:
                            result_data['running'] = res['running']
                        
                        # Extract from ansible_facts if they exist
                        if 'ansible_facts' in res:
                            ansible_facts = res['ansible_facts']
                            if 'running' in ansible_facts:
                                result_data['running'] = ansible_facts['running']
                            if 'interface_status' in ansible_facts:
                                raw = ansible_facts['interface_status']
                                result_data['interface_status'] = raw if isinstance(raw, list) else []
                                if not isinstance(raw, list):
                                    logger.warning(f"interface_status Ansible fact is not a list (got {type(raw).__name__}), ignoring")
                
                logger.info(f"Extracted VyOS status data: running={result_data.get('running')}, "
                           f"interfaces={len(result_data.get('interface_status', []))}")
                
                return {
                    'success': True,
                    **result_data
                }
            else:
                # Extract error information
                error_msg = f"Ansible playbook failed with status: {runner.status}"
                
                # Try to get more detailed error from events
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
                    'error': error_msg
                }
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


#########################################################################
# LinuxNetwork Validation
#########################################################################

async def check_linux_networks_ready(interfaces: list, namespace: str) -> tuple[bool, list]:
    """
    Check if all LinuxNetwork CRs specified in interfaces exist and have status 'Ready'.
    
    Args:
        interfaces: List of interface configurations
        namespace: Kubernetes namespace
    
    Returns:
        tuple: (all_ready: bool, not_ready_networks: list)
    """
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version='google.dev/v1', kind='LinuxNetwork')
    
    not_ready_networks = []
    
    for interface in interfaces:
        network_name = interface.get('linux_network')
        if network_name:
            try:
                network = api.get(name=network_name, namespace=namespace)
                network_dict = network.to_dict()
                
                status = network_dict.get('status', {})
                phase = status.get('phase', 'Unknown')
                
                if phase != 'Ready':
                    logger.warning(f"LinuxNetwork '{network_name}' is in phase '{phase}', not Ready")
                    not_ready_networks.append({
                        'name': network_name,
                        'phase': phase,
                        'message': status.get('message', 'No message')
                    })
                else:
                    logger.info(f"LinuxNetwork '{network_name}' is Ready")

            except kubernetes.client.rest.ApiException as e:
                if e.status == 404:
                    logger.warning(f"LinuxNetwork '{network_name}' not found")
                    not_ready_networks.append({
                        'name': network_name,
                        'phase': 'NotFound',
                        'message': 'LinuxNetwork resource does not exist'
                    })
                else:
                    logger.error(f"Error checking LinuxNetwork '{network_name}': {e}")
                    not_ready_networks.append({
                        'name': network_name,
                        'phase': 'Error',
                        'message': str(e)
                    })
            except Exception as e:
                logger.error(f"Unexpected error checking LinuxNetwork '{network_name}': {e}")
                not_ready_networks.append({
                    'name': network_name,
                    'phase': 'Error',
                    'message': str(e)
                })
    
    return len(not_ready_networks) == 0, not_ready_networks
