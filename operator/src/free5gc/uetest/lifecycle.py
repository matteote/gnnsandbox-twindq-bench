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
from free5gc.uetest.lifecycle_tasks import *
from utils.compute import *
from free5gc.utils.k8s import getDNNAddress, getIMSI
# from graph.lifecycle_tasks import update_network_node

logger = logging.getLogger(__name__)

##########################################
# Create a new UE Test
##########################################
@kopf.on.create('google.dev', 'v1', 'uetest')
async def uetest(spec, meta, status, namespace, name, logger, **kwargs):
  logger.debug(f"Create ue test {name} with spec: {spec}")

  # get the name of the UERanSim VM and check its existence
  ueransim = spec.get('ueransim')
  if ueransim is None:
    raise kopf.PermanentError("ueransim not found in uetest spec")
  
  vmname = ueransim.get('name')
  if vmname is None:
    raise kopf.PermanentError("ueransim name not found in uetest #{name} spec")
  
  if get_compute(namespace, vmname) is None:
    raise kopf.PermanentError("ueransim name #{vmname} not found")

  imsi = await getIMSI(namespace, vmname)
  if imsi is None:
    raise kopf.PermanentError("No imsi found")
  
  dnn = spec.get('datanetwork')
  if dnn is None:
    raise kopf.PermanentError("No data network found")

  dnn_address = await getDNNAddress(namespace, dnn['name'])
  if dnn_address is None:
     raise kopf.PermanentError("No DNN address")

  dnn_url = f"http://{dnn_address}"

  await run_test(namespace, ueransim['name'],imsi ,dnn_url)

  return {
      "status":"Running",
  }

##########################################
# Delete UE Test
##########################################
@kopf.on.delete('google.dev', 'v1', 'uetest')
async def uetest(spec, meta, status, namespace, name, logger, **kwargs):
  logger.debug(f"Delete ue test {name} with spec: {spec}")

  # get the name of the UERanSim VM and check its existence
  ueransim = spec.get('ueransim')
  if ueransim is None:
    raise kopf.PermanentError("ueransim not found in uetest spec")
  
  vmname = ueransim.get('name')
  if vmname is None:
    raise kopf.PermanentError("ueransim name not found in uetest #{name} spec")
  
  if get_compute(namespace, vmname) is None:
    raise kopf.PermanentError("ueransim name #{vmname} not found")

  await stop_test(namespace, vmname)

  return {
      "status":"stopped",
  }

##########################################
# Catch updates on status
##########################################
# @kopf.on.update('uetest', field='status')
# async def uetest_update(body, spec, meta, status, namespace, name, logger, **kwargs):
#   logger.debug(f"Update uetest {name} with spec: {spec} and status: {status['uetest']['status']}")
#   kind = body.get('kind')
#   await update_network_node(body, spec, namespace, name, kind, meta['uid'])