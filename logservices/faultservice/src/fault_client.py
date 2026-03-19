
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
import os
import requests
import logging
import json
import httpx
from a2a.client import A2ACardResolver, A2AClient
from typing import Any
from uuid import uuid4
from incidentdb import IncidentDB
from a2a.types import (
    SendMessageRequest,
    SendMessageSuccessResponse,
    Task,
    MessageSendParams,
)

# Import tracing components from shared library
import sys
sys.path.insert(0, '/app/networkagents/lib/src')

logger = logging.getLogger(__name__)

######################################################################
# Fault Agent Client
######################################################################
class FaultClient:
    _instance = None

    @classmethod
    async def get_instance(cls):
        if FaultClient._instance is None:
            FaultClient._instance = cls()
            await FaultClient._instance.create_client()
        return FaultClient._instance

    def __init__(self):
        """
        Initialize the Resolver Agent client.
        """
        self.address = os.environ.get('RESOLVER_URL')
        self.agent_card = None
        self.agent_client = None

    async def create_client(self):
        """Create an A2A client to connect to the Resolver Agent."""
        logger.info(f"Creating client for Resolver Agent with address {self.address}")
        if self.agent_client is None:
            async with httpx.AsyncClient(timeout=60.0) as httpx_client:
                card_resolver = A2ACardResolver(httpx_client=httpx_client,base_url=self.address)
                self.agent_card = await card_resolver.get_agent_card()
                self.agent_card.url = self.address
                logger.info(f"Connected to agent: {self.agent_card.name}")

            self.agent_client = await A2AClient.get_client_from_agent_card_url(httpx_client=httpx.AsyncClient(timeout=60.0), base_url=self.address)
            # the discovered card address is the internal address of the server, make sure to update with the external address or is not reachable
            self.agent_client.url=self.address

    async def create_send_message_payload(self, data, task_id: str | None = None, context_id: str | None = None) -> dict[str, Any]:
        """Helper function to create the payload for sending a task."""

        logger.info("create request with %s, task_id %s, context_id %s", data, task_id, context_id)

        payload: dict[str, Any] = {
            'message': {
                'role': 'user',
                'parts': [{'kind': 'data', 'data': { 'incident': data } }],
                'messageId': uuid4().hex,
            },
        }
        if task_id is not None:
            payload['message']['taskId'] = task_id

        if context_id:
            payload['message']['contextId'] = context_id

        return payload

    async def send_notification(self, taskid, incident_data):
        logger.info("Sending notification to supervisor for incident update")
        supervisor_url = os.getenv("SUPERVISOR_URL", "http://127.0.0.1:9000")
        if not supervisor_url:
            logger.error("SUPERVISOR_URL environment variable not set")
        else:
            notification_url = f"{supervisor_url}/pushnotification"
            # Create the payload using incident_update state (replaces new_incident)
            payload = {
                "name": "Fault Service",
                "state": "incident_update",
                "task_id": taskid,
                "context_id": taskid,
                "content": "Resolution progress update",
                "input_data": {
                    "incident_data": {"incident": incident_data},
                    "strategy": None,  # No strategy yet in initial notification
                    "root_case": None,  # No root cause yet in initial notification
                    "resolution": None,  # No resolution yet in initial notification
                }
            }
            
            try:
                # Send the POST request
                logger.info(f"Sending notification to {notification_url}")
                response = requests.post(notification_url, json=payload)
                
                # Check if the request was successful
                if response.status_code == 200:
                    logger.info("Notification sent successfully")
                else:
                    logger.error(f"Failed to send notification. Status code: {response.status_code}")
                    logger.error(f"Response: {response.text}")
            except Exception as e:
                logger.error(f"Error sending notification: {str(e)}", exc_info=True)

    async def send_incident_to_resolver(self, incident_data):
        """
        Send incident data to the resolver agent.        
        """
        # create a task id for the resolver agent
        incidentid = uuid4().hex

        # add the id to the incident payload so the agent can find in spanner
        incident_data['jsonPayload']['incident_id']=incidentid

        logger.info("writing incident to db")
        db = await IncidentDB.get_instance()
        await db.create_incident(incident_data['jsonPayload'], incidentid)

        # Create a message with data part - don't specify task_id to let A2A framework create a new task
        send_payload = await self.create_send_message_payload(incident_data['jsonPayload'], context_id=incidentid, task_id=None)
        
        params = MessageSendParams(**send_payload)
                
        logger.info(f"Request parameters: {params}")

        try:
            # notify the supervisor notification endpoint that this incident has happened
            await self.send_notification(incidentid, incident_data['jsonPayload'])

            logger.info("Sending non-streaming request with data part...")
            request = SendMessageRequest(id=uuid4().hex, params=params)
            response = await self.agent_client.send_message(request)
            logger.info(response)
            
        except httpx.HTTPError as e:
            logger.error(f"HTTP error communicating with Resolver Agent: {str(e)}", exc_info=True)
            return None
        except Exception as e:
            logger.error(f"Error communicating with Resolver Agent: {str(e)}", exc_info=True)
            return None
