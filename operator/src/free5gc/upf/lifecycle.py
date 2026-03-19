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
from free5gc.upf.lifecycle_tasks import *
# from graph.lifecycle_tasks import update_network_node

logger = logging.getLogger(__name__)

##########################################
# Create a new userplanefunction
##########################################
@kopf.on.create('google.dev', 'v1', 'userplanefunction')
async def userplanefunction(meta, spec, status, namespace, name, logger, **kwargs):
  logger.debug(f"Create upf {name} with spec: {spec}")

  # get the VPCs to bind UPF to and wait for them to exist if needed
  ingress = spec.get('ingress')
  ingress_name = ingress.get('name') if ingress is not None else None
  if await get_subnetwork(namespace, ingress_name) is None:
    raise kopf.TemporaryError(f"Waiting for subnet {ingress_name}", 20 )
  egress = spec.get('egress')
  egress_name = egress.get('name') if egress is not None else None
  if await get_subnetwork(namespace, egress_name) is None:
    raise kopf.TemporaryError(f"Waiting for subnet {egress_name}", 20 )

  # get monitor and graph labels from the metadata / labels.
  monitor = get_boolean_label(meta, 'monitor')
  graph = get_boolean_label(meta, 'graph')
  
  # create UPF VM on target network 
  await create_compute( namespace, 
                        name, # parent name
                        name,
                        None,
                        [ ingress, egress], # set this to the target network names to bind to
                        os.getenv("GOOGLE_PROJECT"),
                        os.getenv("GOOGLE_REGION"),
                        os.getenv("GOOGLE_ZONE"),
                        monitor=monitor, # set to false so this VM is not scraped by prometheus
                        graph=graph)

  # install UPF to VM 
  await run_install(namespace, name)
  mgmtIP    = await get_ip(namespace, name)
  ingressIP = await get_ip(namespace, name, ingress_name)
  egressIP  = await get_ip(namespace, name, egress_name)

  return {
      "status":"Running",
      "mgmtAddress": mgmtIP,
      "ingressAddress": ingressIP,
      "egressAddress": egressIP
  }

##########################################
# Catch updates on status
##########################################
@kopf.on.update('userplanefunction', field='status')
async def userplanefunction_update(body, spec, meta, status, namespace, name, logger, **kwargs):
  logger.debug(f"Update userplanefunction {name} with spec: {spec} and status: {status['userplanefunction']['status']}")
  kind = body.get('kind')
  # await update_network_node(body, spec, namespace, name, kind, meta['uid'])