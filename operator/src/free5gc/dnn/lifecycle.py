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
from utils.compute import *
from utils.resources import get_boolean_label
import kopf
from free5gc.dnn.lifecycle_tasks import *
# from graph.lifecycle_tasks import update_network_node

logger = logging.getLogger(__name__)

##########################################
# Create a new dnn
##########################################
@kopf.on.create('google.dev', 'v1', 'datanetwork')
async def datanetwork(spec, meta, status, namespace, name, logger, **kwargs):
  logger.debug(f"Create datanetwork {name} with spec: {spec}")

  # get the VPC name to bind UPF to
  network_interface = spec.get('interface')
  if network_interface is None:
    raise kopf.PermanentError("No interface found")
  
  # get monitor and graph labels from the metadata / labels.
  monitor = get_boolean_label(meta, 'monitor')
  graph = get_boolean_label(meta, 'graph')

  # create UPF VM on target network 
  await create_compute( namespace, 
                        name, # parent name
                        name,
                        None,
                        [network_interface], # set this to the target network name to bind to
                        os.getenv("GOOGLE_PROJECT"),
                        os.getenv("GOOGLE_REGION"),
                        os.getenv("GOOGLE_ZONE"), 
                        monitor=monitor, # set to false so this VM is not scraped by prometheus
                        graph=graph)

  # install UPF to VM 
  await run_install(namespace, name)
  ip = await get_ip(namespace, name, network_interface.get('name'))

  return {
      "status":"Running",
      "address": ip
  }

##########################################
# Catch updates on status
##########################################
@kopf.on.update('google.dev', 'v1', 'datanetwork', field='status')
async def datanetwork_update(body, spec, meta, status, namespace, name, logger, **kwargs):
  logger.debug(f"Update datanetwork {name} with spec: {spec} and status: {status['datanetwork']['status']}")
  kind = body.get('kind')
  # await update_network_node(body, spec, namespace, name, kind, meta['uid'])

