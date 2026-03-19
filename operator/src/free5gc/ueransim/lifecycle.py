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
import kopf
from utils.compute import *
from free5gc.ueransim.lifecycle_tasks import *
from utils.resources import get_boolean_label
# from graph.lifecycle_tasks import update_network_node

logger = logging.getLogger(__name__)

##########################################
# Create a new ueransim
##########################################
@kopf.on.create('google.dev', 'v1', 'ueransim')
async def ueransim(spec, meta, status, namespace, name, logger, **kwargs):
  logger.debug(f"Create ueransim {name} with spec: {spec}")

  # Validate spec and extract parameters
  validated_params = validate_and_extract_ueransim_params(spec)
  
  # get monitor and graph labels from the metadata / labels.
  monitor = get_boolean_label(meta, 'monitor')
  graph = get_boolean_label(meta, 'graph')

  try:
    # create UERANSIM VM on target network 
    await create_compute( namespace, 
                          name, # parent name
                          name, # vm name
                          None, # external IP
                          [validated_params['network_interface']], # set this to the target network name to bind to
                          os.getenv("GOOGLE_PROJECT"),
                          os.getenv("GOOGLE_REGION"),
                          os.getenv("GOOGLE_ZONE"), 
                          monitor=monitor, # set to false so this VM is not scraped by prometheus
                          graph=graph)

    # Install UERANSIM using common installation function
    await setup_ueransim_installation(namespace, name, validated_params)

    return {
        "status":"Running", 
    }

  except kubernetes.dynamic.exceptions.ResourceNotFoundError as e:
    raise kopf.TemporaryError("Amf not running yet", 30)


##########################################
# Catch updates on status
##########################################
@kopf.on.update('google.dev', 'v1', 'ueransim', field='status')
async def ueransim_update(body, spec, meta, status, namespace, name, logger, **kwargs):
  logger.debug(f"Update ueransim {name} with spec: {spec} and status: {status['ueransim']['status']}")
  kind = body.get('kind')
  # await update_network_node(body, spec, namespace, name, kind, meta['uid'])

##########################################
# Watch for Failed UERANSIM
##########################################
@kopf.on.field('google.dev', 'v1', 'ueransim', field='status.ueransim.status')
async def handle_ueransim_status_change(old, new, spec, status, namespace, name, body, **kwargs):
    """
    Handler that watches for status field changes and triggers re-installation
    when status changes from Running to Failed
    """
    logger.info(f"UERANSIM status change detected for {name}: {old} -> {new}")
    
    # Check if this is a Running -> Failed transition
    if old == "Running" and new == "Failed":
        logger.warning(f"Detected failure in UERANSIM {name}, triggering re-installation...")
        
        try:
            # Update status to indicate re-installation is starting
            await update_ueransim_status(namespace, name, "Reinstalling", "Re-installation triggered due to failure")
            
            # Trigger the installation process
            await trigger_ueransim_reinstallation(spec, namespace, name, body)
            
            # Update status to Running after successful installation
            await update_ueransim_status(namespace, name, "Running", "Re-installation completed successfully")
            
        except Exception as e:
            logger.error(f"Re-installation failed for UERANSIM {name}: {e}")
            await update_ueransim_status(namespace, name, "Failed", f"Re-installation failed: {str(e)}")
            raise kopf.TemporaryError(f"Re-installation failed: {e}", delay=60)



