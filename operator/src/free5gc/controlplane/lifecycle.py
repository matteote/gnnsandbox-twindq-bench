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
import kopf
from free5gc.utils.k8s import getDNNAddress, getUPFAddress
from free5gc.controlplane.lifecycle_tasks import *
# from graph.lifecycle_tasks import update_network_node

logger = logging.getLogger(__name__)

##########################################
# Create a new controlplane
##########################################
@kopf.on.create('google.dev', 'v1', 'controlplane')
async def controlplane(spec, status, namespace, name, logger, **kwargs):
  logger.info(f"Create control plane with spec: {spec} in namespace {namespace}")

  # get the VPC to bind to
  network = spec.get('network')

  await create_compute( namespace, 
                        name, # parent name
                        name,
                        None,
                        [network], # set this to the target network names to bind to
                        os.getenv("GOOGLE_PROJECT"),
                        os.getenv("GOOGLE_REGION"),
                        os.getenv("GOOGLE_ZONE"), 
                        graph=True,
                        monitor=True) 

  # get UPF address
  upf = spec.get("upf")
  upfAddress = await getUPFAddress(namespace, upf['name'])
  if upfAddress is None:
    raise kopf.PermanentError("no UPF address")

  # get the DNN VM address and wait until it comes up
  dnn = spec.get("dnn")
  dnnAddress = await getDNNAddress(namespace, dnn['name'])
  if dnnAddress is None:
    raise kopf.PermanentError("no DNN address")

  # get the external ip address of the VM
  vmMgmtAddress = await get_ip(namespace, name)
  logger.debug("mgmt address %s", vmMgmtAddress)
  if vmMgmtAddress is None:
    raise kopf.PermanentError("No VM mgmt found")

  vmDataAddress = await get_ip(namespace, name, network['name'])
  logger.debug("external address %s", vmDataAddress)
  if vmDataAddress is None:
    raise kopf.PermanentError("No VM data address found")

  await run_install(namespace, name, vmDataAddress, upfAddress, dnnAddress)

  return {
      "status":"Running",
      "mgmtAddress": vmMgmtAddress,
      "dataAddress": vmDataAddress,
      "amfPort": 38412,
      "webuiAddress": f"http://{vmMgmtAddress}:5000"
  }

##########################################
# Catch updates on status
##########################################
@kopf.on.update('google.dev', 'v1', 'controlplane', field='status')
async def controlplane_update(body, spec, meta, status, namespace, name, logger, **kwargs):
  logger.debug(f"Update controlplane {name} with spec: {spec} and status: {status['controlplane']['status']}")
  kind = body.get('kind')
  # await update_network_node(body, spec, namespace, name, kind, meta['uid'])