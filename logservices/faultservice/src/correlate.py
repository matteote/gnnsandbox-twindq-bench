
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
from incidentdb import IncidentDB
import logging
logger = logging.getLogger(__name__)

async def correlate_incident(incident_data):
    """Correlate an incident to an existing one or create a new one."""
    logger.info(f"checking {incident_data} ")

    json_payload = incident_data.get('jsonPayload', {})
    labels = incident_data.get('labels', {})
    python_logger = labels.get('python_logger')

    # This is a simple correlation logic. In a real-world scenario, this would be much more complex.
    if python_logger == 'UERANSIMHEALTH':
        logger.info("Checking UE RAN SIM Health incident")
        process_name = json_payload.get('process_name')
        hostname = json_payload.get('hostname')
        if process_name and hostname:
            db = await IncidentDB.get_instance()
            existing_incident = await db.get_incident_by_issue(
                'process_name', process_name, 'hostname', hostname
            )
            if existing_incident:
                return existing_incident
    elif python_logger == 'CRITICALSERVICEERROR':
        logger.info("Checking Critical Service Error incident")
        node = json_payload.get('node')
        userid = json_payload.get('userid')
        if node and userid:
            db=await IncidentDB.get_instance()
            existing_incident = await db.get_incident_by_issue(
                'node', node, 'userid', userid
            )
            if existing_incident:
                return existing_incident

    return None
