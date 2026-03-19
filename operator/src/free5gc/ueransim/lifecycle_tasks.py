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

import logging
import os
import asyncio
import kopf
from utils.compute import *
import ansible_runner
from utils.ansible import event_handler

logger = logging.getLogger(__name__)

########################################################
# Get the ip and port for control plane
########################################################
async def get_controlplane_addresses(namespace, name):
    logger.debug("get controlplane address %s", name)

    api = get_resource_api(api_version="v1", kind="ControlPlane")
    try:
        result = api.get(name=name, namespace=namespace)

        # get the ip and port on the status field
        if result.get('status') and result.get('status').get('controlplane'):
            addresses = result.get('status')['controlplane']
            logger.debug("controlplane address = %s", addresses)
            return addresses
        else:
            raise kopf.TemporaryError("Control Plane status not ready yet. Waiting...", delay=10)

    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            logger.debug("%s Not found", name)
            raise kopf.TemporaryError(f"ControlPlane resource {name} not found. Waiting...", delay=10)
        else:
            logger.error(e)
            raise

########################################################
# Install UERANSIM to VM
########################################################
async def run_install(namespace, upf_vm_name, amfAddress, amfPort, webAddress, cellid, ue):
    logger.debug("Installing ueransim")

    ip_address = await get_ip(namespace, upf_vm_name)
    if ip_address is None:
        raise kopf.TemporaryError("waiting for ip address", delay=15)

    # run ansible playbook to install prometheus on the VM
    extravars = {
        'GOOGLE_PROJECT': os.getenv("GOOGLE_PROJECT"),
        'GOOGLE_REGION': os.getenv("GOOGLE_REGION"),
        'GOOGLE_ZONE': os.getenv("GOOGLE_ZONE"),
        'BASEDIR': constants.basedir, 
        'VMNAME': upf_vm_name,
        'amfAddress': amfAddress,
        'amfPort': amfPort,
        'webAddress': webAddress,
        'imsi': ue['imsi'],
        'plmnId': ue['plmnId'] ,
        'msisdn': ue['msisdn'],
        'cellid': cellid
    }
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
    logger.debug(hosts)
    logger.debug(extravars)

    def run_ansible():
        """Wrapper function to run ansible_runner.run_async"""
        thread, runner = ansible_runner.run_async(
            private_data_dir=constants.basedir+"/free5gc/ueransim/playbooks", 
            inventory={'all': hosts},
            playbook='install.yaml',
            event_handler=event_handler,
            extravars=extravars
        )
        # Wait for the thread to complete
        thread.join()
        return runner

    # Execute in thread pool to avoid blocking the async event loop
    loop = asyncio.get_event_loop()
    r = await loop.run_in_executor(None, run_ansible)

    logger.info("status = %s", r.status)
    if r.status != 'successful':
        raise kopf.TemporaryError("Ansible Error.", delay=15)
    
#########################################################################
# Common helper functions to eliminate code duplication
#########################################################################
def validate_and_extract_ueransim_params(spec):
    """
    Validate spec parameters and extract required values for UERANSIM
    """
    # get the cell id
    cellid = spec.get('cellid')
    if cellid is None:
        raise kopf.PermanentError("no cell id provided. cant continue")

    # get the VPC name to bind UERANSIM to
    network_interface = spec.get('interface')
    if network_interface is None:
        raise kopf.PermanentError("No interface found")

    controlplane_spec = spec.get('controlplane')
    if controlplane_spec is None:
        raise kopf.PermanentError("No control plane details found")

    ue_spec = spec.get('ue')
    if ue_spec is None:
        raise kopf.PermanentError("No ue details found")

    # get the controlplane instance name
    controlplaneName = controlplane_spec.get('name')
    if controlplaneName is None:
        raise kopf.PermanentError("controlplane name needs to be specified")

    return {
        'cellid': cellid,
        'network_interface': network_interface,
        'controlplane_spec': controlplane_spec,
        'controlplaneName': controlplaneName,
        'ue_spec': ue_spec
    }

async def setup_ueransim_installation(namespace, name, validated_params):
    """
    Handle the UERANSIM installation process
    """
    # Get control plane addresses
    controlplaneAddresses = await get_controlplane_addresses(namespace, validated_params['controlplaneName'])
    if controlplaneAddresses is None:
        raise kopf.TemporaryError("Waiting for control plane...", 20)

    # Install UERANSIM to VM
    await run_install(
        namespace, 
        name, 
        controlplaneAddresses['dataAddress'], 
        controlplaneAddresses['amfPort'], 
        controlplaneAddresses['webuiAddress'], 
        validated_params['cellid'], 
        validated_params['ue_spec']
    )

##########################################
# Trigger UERANSIM re-installation
##########################################
async def trigger_ueransim_reinstallation(spec, namespace, name, body):
    """
    Trigger the re-installation process for UERANSIM
    """
    logger.info(f"Starting re-installation for UERANSIM {name}")
    
    # Validate spec and extract parameters (reuse validation logic)
    validated_params = validate_and_extract_ueransim_params(spec)
    
    # Re-run the installation using common setup function
    await setup_ueransim_installation(namespace, name, validated_params)
    
    logger.info(f"Re-installation completed for UERANSIM {name}")

##########################################
# Patch restart after failure status
##########################################
async def update_ueransim_status(namespace, name, status_value, message):
    """
    Update the status of the UERANSIM resource
    """
    import kubernetes
    from datetime import datetime
    
    client = kubernetes.dynamic.DynamicClient(kubernetes.client.ApiClient())
    api = client.resources.get(api_version="google.dev/v1", kind="UERanSIM")
    
    # Get current resource
    resource = api.get(name=name, namespace=namespace)
    resource_dict = resource.to_dict()
    
    # Update status
    if 'status' not in resource_dict:
        resource_dict['status'] = {}
    if 'ueransim' not in resource_dict['status']:
        resource_dict['status']['ueransim'] = {}
    
    resource_dict['status']['ueransim']['status'] = status_value
    resource_dict['status']['ueransim']['message'] = message
    resource_dict['status']['ueransim']['lastUpdated'] = datetime.utcnow().isoformat()
    
    # Patch the resource
    api.patch(
        body=resource_dict, 
        name=name, 
        namespace=namespace, 
        content_type='application/merge-patch+json'
    )
