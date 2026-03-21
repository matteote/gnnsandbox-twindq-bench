import asyncio
import ansible_runner
import os
import utils.constants as constants
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

#########################################################################
# Ansible-based Device Management
#########################################################################

async def create_device(networkvm_ip_address:str, name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """Create a Device using Ansible"""
    logger.info(f"Creating Device: {name}")

    # Extract required fields from spec
    device_name = name
    network_name = spec.get('network_name')
    ip_address = spec.get('ip_address')
    vlan = spec.get('vlan')  # Optional VLAN ID
    gateway = spec.get('gateway')  # Optional gateway IP
    
    # Prepare extra variables for Ansible playbook
    extravars = {
        'operation': 'create',
        'device_name': device_name,
        'network_name': network_name,
        'ip_address': ip_address
    }
    
    # Add VLAN if specified
    if vlan:
        extravars['vlan'] = vlan
    else:
        extravars['vlan'] = ''
    
    # Add gateway if specified
    if gateway:
        extravars['gateway'] = gateway
    else:
        extravars['gateway'] = ''

    result = await _run_ansible_playbook(networkvm_ip_address, 'device.yaml', extravars)
    logger.info(f"Device creation result: {result}")

    if result['success']:
        return {
            'success': True,
        }
    else:
        return {
            'success': False,
            'error': result.get('error', 'Unknown error during Device creation')
        }

async def delete_device(networkvm_ip_address:str, name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """Delete a Device container using Ansible"""
    logger.info(f"Deleting Device: {name}")

    extravars = {
        'operation': 'delete',
        'device_name': name,
        'network_name': spec.get('network_name'),
        'ip_address': spec.get('ip_address')
    }
    
    # Add VLAN if specified
    vlan = spec.get('vlan')
    if vlan:
        extravars['vlan'] = vlan

    result = await _run_ansible_playbook(networkvm_ip_address,'device.yaml', extravars)

    return {
        'success': result['success'],
        'error': result.get('error') if not result['success'] else None
    }


#########################################################################
# Ansible Execution Helper
#########################################################################

async def _run_ansible_playbook(networkvm_ip_address:str, playbook: str, extravars: Dict[str, Any]) -> Dict[str, Any]:
    """Run an Ansible playbook with the given extra variables"""
    
    # Get the Ansible semaphore for throttling
    from utils.ansible import get_ansible_semaphore
    semaphore = get_ansible_semaphore()
    
    # Prepare host inventory
    hosts = {
        'hosts': {
            "monitor": {
                'ansible_host': networkvm_ip_address,
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
                project_dir=constants.basedir + "/device/playbooks",
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
        temp_dir = tempfile.mkdtemp(prefix="ansible_device_")
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
                        
                        # Extract network information from Ansible results
                        if 'network_id' in res:
                            result_data['network_id'] = res['network_id']
                        if 'created_at' in res:
                            result_data['created_at'] = res['created_at']
                        if 'exists' in res:
                            result_data['exists'] = res['exists']
                        if 'network_info' in res:
                            result_data['network_info'] = res['network_info']
                
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
                        if 'stderr' in res and res['stderr']:
                            error_msg = res['stderr']
                        elif 'msg' in res:
                            error_msg = res['msg']
                        break
                
                logger.error(f"Ansible playbook execution failed: {error_msg}")
                return {
                    'success': False,
                    'error': error_msg
                }
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
