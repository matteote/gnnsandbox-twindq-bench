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
import asyncio
import os
import kopf
from utils.compute import *
import ansible_runner
from utils.ansible import event_handler

logger = logging.getLogger(__name__)


########################################################
# Install UPF to VM
########################################################
async def run_install(namespace, upf_vm_name):
    logger.debug("Installing upf")

    ip_address = await get_ip(namespace, upf_vm_name)
    if ip_address is None:
        raise kopf.TemporaryError("waiting for ip address", delay=15)

    # run ansible playbook to install prometheus on the VM
    extravars = {
        'GOOGLE_PROJECT': os.getenv("GOOGLE_PROJECT"),
        'GOOGLE_REGION': os.getenv("GOOGLE_REGION"),
        'GOOGLE_ZONE': os.getenv("GOOGLE_ZONE"),
        'BASEDIR': constants.basedir,
        'VMNAME': upf_vm_name
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
            private_data_dir=constants.basedir+"/free5gc/upf/playbooks", 
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
