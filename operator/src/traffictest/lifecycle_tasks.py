import asyncio
import ansible_runner
import os
import utils.constants as constants
import logging
from typing import Dict, List, Any
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

#########################################################################
# Ansible-based TrafficTest Management
#########################################################################

async def create_traffic_test(name: str, networkvm_ip_address:str, spec: Dict[str, Any], devices_info: Dict[str, Any]) -> Dict[str, Any]:
    """Create a TrafficTest using Ansible - runs playbook once per source device"""

    # Extract common fields from spec
    destination_device = spec.get('destination_device')
    destination_ip = devices_info[destination_device]['ip']
    source_devices = spec.get('source_devices')

    logger.info(f"Creating TrafficTest: {len(source_devices)} source(s) -> {destination_device} ({destination_ip})")

    protocol = spec.get('protocol', 'TCP')
    duration = spec.get('duration', 60)
    bandwidth = spec.get('bandwidth', '10Mbps')
    
    # Traffic pattern configuration
    pattern_type = spec.get('pattern_type', 'constant')
    pattern_config = spec.get('pattern_config', {})
    concurrent_users = spec.get('concurrent_users', 1)
    session_duration = spec.get('session_duration')
    think_time = spec.get('think_time', 0)
    
    # Metrics configuration
    metrics_enabled = spec.get('metrics_enabled', True)
    metrics_interval = spec.get('metrics_interval', 5)
    
    start_time = datetime.now(timezone.utc).isoformat()
    
    # Run Ansible playbook once for each source device
    # Each source gets its own unique port (assigned in lifecycle.py)
    failed_sources = []
    for source_device in source_devices:
        # Get the unique port assigned to this source
        port = devices_info[source_device]['port']
        logger.info(f"Starting traffic test for source: {source_device} on port {port}")
        
        # Prepare extra variables for this specific source device
        extravars = {
            'operation': 'create',
            'test_name': f"{name}_{source_device}",  # Unique name per source
            'source_device': source_device,
            'source_ip': devices_info[source_device]['ip'],
            'destination_device': destination_device,
            'destination_ip': destination_ip,
            'protocol': protocol,
            'dest_port': port,
            'duration': duration,
            'bandwidth': bandwidth,
            'pattern_type': pattern_type,
            'pattern_config': pattern_config,
            'concurrent_users': concurrent_users,
            'session_duration': session_duration,
            'think_time': think_time,
            'metrics_enabled': metrics_enabled,
            'metrics_interval': metrics_interval,
            'start_time': start_time,
        }

        result = await _run_ansible_playbook(networkvm_ip_address, 'traffic_create.yaml', extravars)
        
        if not result['success']:
            logger.error(f"Failed to start traffic test for source {source_device}: {result.get('error')}")
            failed_sources.append(source_device)

    # Return overall result
    if failed_sources:
        if len(failed_sources) == len(source_devices):
            # All sources failed
            return {
                'success': False,
                'error': f'All source devices failed to start: {", ".join(failed_sources)}'
            }
        else:
            # Some sources failed
            return {
                'success': True,  # Partial success
                'start_time': start_time,
                'message': f'Traffic test started with {len(source_devices) - len(failed_sources)}/{len(source_devices)} sources. Failed: {", ".join(failed_sources)}'
            }
    else:
        # All sources succeeded
        return {
            'success': True,
            'start_time': start_time,
            'message': f'Traffic test started successfully with all {len(source_devices)} source(s)'
        }

async def delete_traffic_test(name: str, networkvm_ip_address:str,spec: Dict[str, Any], devices_info: Dict[str, Any]) -> Dict[str, Any]:
    """Delete a TrafficTest using Ansible - runs playbook once per source device"""
    source_devices = spec.get('source_devices', [])
    destination_device = spec.get('destination_device')
    destination_ip = devices_info[destination_device]['ip']
    logger.info(f"Deleting TrafficTest: {len(source_devices)} source(s) -> {destination_device}")
    
    end_time = datetime.now(timezone.utc).isoformat()
    
    # Run delete for each source device
    failed_deletes = []
    for source_device in source_devices:
        port = devices_info[source_device]['port']
        logger.info(f"Deleting traffic test for source: {source_device} on port {port}")
        
        extravars = {
            'operation': 'delete',
            'test_name': f"{name}_{source_device}",  # Unique name per source
            'source_device': source_device,
            'destination_device': destination_device,
            'destination_ip': destination_ip,
            'protocol': spec.get('protocol', 'TCP'),
            'dest_port': port,  # Use the same port that was assigned during create
            'end_time': end_time
        }

        result = await _run_ansible_playbook(networkvm_ip_address,'traffic_delete.yaml', extravars)
        
        if not result['success']:
            logger.warning(f"Failed to delete traffic test for source {source_device}: {result.get('error')}")
            failed_deletes.append(source_device)

    return {
        'success': len(failed_deletes) == 0,
        'error': f"Failed to delete {len(failed_deletes)} source(s): {', '.join(failed_deletes)}" if failed_deletes else None,
        'end_time': end_time
    }

async def get_traffic_test_status(name: str, networkvm_ip_address:str, spec: Dict[str, Any], devices_info: Dict[str, Any]) -> Dict[str, Any]:
    """Get current status of a TrafficTest using Ansible - queries each source device separately"""
    source_devices = spec.get('source_devices', [])
    destination_device = spec.get('destination_device')
    destination_ip = devices_info[destination_device]['ip']
    logger.info(f"Getting TrafficTest '{name}' status: {len(source_devices)} source(s) -> {destination_device}")
    
    # Collect status from each source device
    source_statuses = {}
    aggregate_metrics = {
        'total_throughput_bps': 0,
        'avg_latency_ms': 0,
        'avg_packet_loss_pct': 0,
        'total_connections': 0
    }
    
    successful_queries = 0
    
    for source_device in source_devices:
        port = devices_info[source_device]['port']
            
        extravars = {
            'operation': 'status',
            'source_device': source_device,
            'destination_device': destination_device,
            'destination_ip': destination_ip,
            'protocol': spec.get('protocol', 'TCP'),
            'dest_port': port,
        }

        result = await _run_ansible_playbook(networkvm_ip_address,'traffic_status.yaml', extravars)
        
        if result['success']:
            metrics = result.get('current_metrics', {})
            source_statuses[source_device] = {
                'phase': result.get('status', 'Unknown'),
                'message': result.get('message', ''),
                'metrics': metrics
            }
            
            # Aggregate metrics
            aggregate_metrics['total_throughput_bps'] += metrics.get('throughput_bps', 0)
            aggregate_metrics['avg_latency_ms'] += metrics.get('latency_ms', 0)
            aggregate_metrics['avg_packet_loss_pct'] += metrics.get('packet_loss_pct', 0)
            aggregate_metrics['total_connections'] += metrics.get('active_connections', 0)
            successful_queries += 1
        else:
            logger.warning(f"Failed to get status for source {source_device}: {result.get('error')}")
            source_statuses[source_device] = {
                'phase': 'Unknown',
                'message': f"Failed to query: {result.get('error', 'Unknown error')}",
                'metrics': {}
            }
    
    # Calculate averages
    if successful_queries > 0:
        aggregate_metrics['avg_latency_ms'] /= successful_queries
        aggregate_metrics['avg_packet_loss_pct'] /= successful_queries

    return {
        'success': successful_queries > 0,
        'source_statuses': source_statuses,
        'aggregate_metrics': aggregate_metrics,
        'message': f'Status retrieved for {successful_queries}/{len(source_devices)} source(s)'
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
    
    def run_ansible():
        """Wrapper function to run ansible_runner.run_async"""
        try:
            thread, runner = ansible_runner.run_async(
                private_data_dir=constants.basedir + "/traffictest/playbooks",
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
        # Execute in thread pool to avoid blocking the async event loop
        loop = asyncio.get_event_loop()
        runner = await loop.run_in_executor(None, run_ansible)
        
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
                    
                    # Extract traffic test information from Ansible results
                    if 'traffic_test_id' in res:
                        result_data['traffic_test_id'] = res['traffic_test_id']
                    if 'start_time' in res:
                        result_data['start_time'] = res['start_time']
                    if 'end_time' in res:
                        result_data['end_time'] = res['end_time']
                    if 'status' in res:
                        result_data['status'] = res['status']
                    if 'current_metrics' in res:
                        result_data['current_metrics'] = res['current_metrics']
                    if 'message' in res:
                        result_data['message'] = res['message']
                    if 'results_file' in res:
                        result_data['results_file'] = res['results_file']
            
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
