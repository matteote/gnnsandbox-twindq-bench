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
from utils.compute import *
from gitea.lifecycle_tasks import *

logger = logging.getLogger(__name__)

@kopf.on.create('google.dev','v1','gitea')
async def create_gitea(spec, meta, name, namespace, logger, **kwargs):
    """
    On gitea resource creation, create the supporting VM, install the gitea
    app, run the playbooks to create the various git repos and create the
    root sync object in K8s config sync
    """
    logger.info("Create gitea repo")

    # Create external IP address
    await create_external_ip(namespace, "gitea", os.getenv("GOOGLE_REGION"), graph=False)
    external_ip_address = await get_ip_address(namespace, "gitea")

    # Create VM and attach IP address
    await create_compute(namespace, 
                         name,
                         "gitea",
                         external_ip_address, # replace with None when only private IP address
                         None, 
                         os.getenv("GOOGLE_PROJECT"),
                         os.getenv("GOOGLE_REGION"),
                         os.getenv("GOOGLE_ZONE"), 
                         family="ubuntu-os-cloud",
                         release="ubuntu-2204-lts",
                         monitor=False, # set to false so this VM is not scraped by prometheus
                         graph=False, # set to false so this VM is not showing on topology graph
                         scopes="cloud-platform")
    # Install Gitea
    await run_gitea_install(namespace, external_ip_address)

    # Create the private key
    # await create_root_sync_private_key()

    # Create root sync object
    await create_root_sync(external_ip_address)

    return {"status": "Running", "external_ip_address": external_ip_address}

