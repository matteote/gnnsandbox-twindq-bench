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
import json
import base64
import asyncio
import sys
from aiohttp import web
import aiohttp_cors
from fault_client import FaultClient
from correlate import correlate_incident
from incidentdb import IncidentDB

log_format = "%(asctime)s::%(levelname)s::%(name)s::"\
             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level=logging.INFO, format=log_format)
logger = logging.getLogger(__name__)

# Initialize aiohttp application with no middleware
app = web.Application()

# Setup CORS for aiohttp routes
cors = aiohttp_cors.setup(app, defaults={
    "*": aiohttp_cors.ResourceOptions(
        allow_credentials=True,
        expose_headers="*",
        allow_headers="*",
        allow_methods="*"
    )
})

######################################################################
# Decode message
######################################################################
def decode_pubsub_message(message_data):
    logger.info("decode pubsub message")
    """Decode the Pub/Sub message data from base64."""
    try:
        if message_data:
            decoded_data = base64.b64decode(message_data).decode('utf-8')
            return decoded_data
    except Exception as e:
        logger.error(f"Error decoding message data: {e}")
        return None

######################################################################
# Process the fault event
######################################################################

# Global dictionary to store locks for each correlation key
correlation_locks = {}
# Lock to protect the correlation_locks dictionary itself
locks_lock = asyncio.Lock()

def get_correlation_key(log_entry):
    """Extract a unique correlation key from the log entry."""
    json_payload = log_entry.get('jsonPayload', {})
    labels = log_entry.get('labels', {})
    python_logger = labels.get('python_logger')

    if python_logger == 'UERANSIMHEALTH':
        process_name = json_payload.get('process_name')
        hostname = json_payload.get('hostname')
        if process_name and hostname:
            return f"UERANSIMHEALTH:{process_name}:{hostname}"
    elif python_logger == 'CRITICALSERVICEERROR':
        node = json_payload.get('node')
        userid = json_payload.get('userid')
        if node and userid:
            return f"CRITICALSERVICEERROR:{node}:{userid}"
    return None

async def get_lock_for_key(key):
    """Get or create a lock for the given key."""
    async with locks_lock:
        if key not in correlation_locks:
            correlation_locks[key] = asyncio.Lock()
        return correlation_locks[key]

async def process_fault_event(event_data):
    logger.info("process fault event")
    """Process the fault event and extract relevant information."""
    try:
        # Parse the log entry if it's JSON
        if event_data.startswith('{'):
            log_entry = json.loads(event_data)
            logger.info(log_entry)
            
            # Extract relevant fields from the log entry
            timestamp = log_entry.get('timestamp', 'Unknown')
            json_payload = log_entry.get('jsonPayload', {})
            
            logger.info(f"=== FAULT EVENT DETECTED ===")
            logger.info(f"Timestamp: {timestamp}")
            logger.info(f"JSON Payload: {json_payload}")
            logger.info(f"=== END FAULT EVENT ===")
            
            # Get correlation key
            correlation_key = get_correlation_key(log_entry)
            
            if correlation_key:
                lock = await get_lock_for_key(correlation_key)
                async with lock:
                    # Check of this error has already triggered an incident
                    incident_exists = await correlate_incident(log_entry)
                    if incident_exists:
                        logger.info(f"Incident already open for {correlation_key}, not sending to resolver.")
                    else:
                        logger.info(f"No open incident found for {correlation_key}, sending to resolver.")
                        agent = await FaultClient.get_instance()
                        await agent.send_incident_to_resolver(log_entry)
            else:
                # Fallback for events without a correlation key (shouldn't happen for known types)
                logger.warning("No correlation key found, processing without lock.")
                incident_exists = await correlate_incident(log_entry)
                if incident_exists:
                    logger.info("Incident already open, not sending to resolver.")
                else:
                    logger.info("No open incident found, sending to resolver.")
                    agent = await FaultClient.get_instance()
                    await agent.send_incident_to_resolver(log_entry)
        else:
            # Handle plain text log entries
            logger.info(f"=== FAULT EVENT (TEXT) ===")
            logger.info(f"Event Data: {event_data}")
            logger.info(f"=== END FAULT EVENT ===")
            
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing JSON log entry: {e}")
        logger.info(f"Raw event data: {event_data}")
    except Exception as e:
        logger.error(f"Error processing fault event: {e}")

######################################################################
# Handle basic eventarc 
######################################################################
async def handle_eventarc(request):
    logger.info("EVENT Received")
    """Handle incoming Eventarc events from Pub/Sub."""
    try:        
        # Get the request data
        event_data = await request.json()
        
        if event_data:
            logger.info(f"Event data: {json.dumps(event_data, indent=2)}")
            
            # Extract the Pub/Sub message
            message = event_data.get('message', {})
            if message:
                # Decode the message data
                message_data = message.get('data', '')
                decoded_message = decode_pubsub_message(message_data)
                
                if decoded_message:
                    logger.info(f"Decoded message: {decoded_message}")
                    await process_fault_event(decoded_message)
        
        return web.json_response({'status': 'success', 'message': 'Event processed'})
        
    except Exception as e:
        logger.error(f"Error handling Eventarc event: {e}")
        return web.json_response({'status': 'error', 'message': str(e)}, status=500)

######################################################################
# Health check
######################################################################
async def health_check(request):
    """Health check endpoint."""
    return web.json_response({'status': 'healthy', 'service': 'fault-service'})

######################################################################
# basic info
######################################################################
async def root(request):
    """Root endpoint for basic info."""
    return web.json_response({
        'service': 'Network Fault Service',
        'status': 'running',
        'description': 'Processes fault events from UERANSIM health monitoring'
    })

######################################################################
# Setup
######################################################################
async def init():
    route = app.router.add_post('/', handle_eventarc)
    cors.add(route)
    route = app.router.add_get('/health', health_check)
    cors.add(route)
    route = app.router.add_get('/', root)
    cors.add(route)

    runner = web.AppRunner(app)
    await runner.setup()

    port = 8080
    if os.getenv("DEBUG") is not None:
        port = 9010

    logger.info("starting server on port %s",port)
    site = web.TCPSite(runner, host="0.0.0.0", port=port, ssl_context=None)
    await site.start()
    
######################################################################
# Main
######################################################################
if __name__ == "__main__":
    logger.info("starting network agent...")

    if "RESOLVER_URL" not in os.environ:
        logger.critical("RESOLVER_URL not set, exiting")
        sys.exit(1)
    if "SUPERVISOR_URL" not in os.environ:
        logger.critical("SUPERVISOR_URL not set, exiting")
        sys.exit(1)
    
    loop=asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(init())
    loop.run_forever()
