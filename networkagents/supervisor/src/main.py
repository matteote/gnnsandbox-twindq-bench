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

import asyncio
import socketio
from aiohttp import web
import aiohttp_cors
import logging
import os
import json
from tools.topology import build_graph, spanner_connect
from tools.metrics import fetch_all_last_metrics
from tools.logs import fetch_log_entries
from tools.traces import TraceStreamListener
from tools.networkdescriptors import initialise_default_network
from endpoints.socketendpoint import clients_state, view_to_edge_label_map


log_format = "%(asctime)s::%(levelname)s::%(name)s::"\
             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level=logging.INFO, format=log_format)
logger = logging.getLogger(__name__)
BASE_DIR = os.path.dirname(os.path.realpath(__file__))

# Initialize Socket.IO server with CORS enabled for all origins
sio = socketio.AsyncServer(
    async_mode='aiohttp',
    cors_allowed_origins="*",
    logger=False,
    engineio_logger=False
)

# Initialize aiohttp application with no middleware
app = web.Application()
sio.attach(app)

# Setup CORS for aiohttp routes
cors = aiohttp_cors.setup(app, defaults={
    "*": aiohttp_cors.ResourceOptions(
        allow_credentials=True,
        expose_headers="*",
        allow_headers="*",
        allow_methods="*"
    )
})

# Global trace listener instance
trace_listener = None

async def init():
    global trace_listener
    
    runner = web.AppRunner(app)
    await runner.setup()

    port = 8080
    if os.getenv("DEBUG") is not None:
        port = 9000

    logger.info("starting server on port %s",port)
    site = web.TCPSite(runner, host="0.0.0.0", port=port, ssl_context=None)
    await site.start()

    # Initialise the default network descriptor in Spanner if not present.
    # Run in an executor because the Spanner calls are synchronous.
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, initialise_default_network)
    
    # Trace listener was already initialized before SocketEndpoint creation
    # Just log confirmation
    if trace_listener:
        logger.info(f"✓ Trace stream listener ready: {trace_listener} (will start when clients enable traces)")
    else:
        logger.error("Could not initialize trace listener")

if __name__ == "__main__":
    logger.info("starting network agent...")
    
    import endpoints
    
    # Create SocketEndpoint first
    socketEndpoint = endpoints.SocketEndpoint(sio)
    
    # Initialize trace_listener with the socket endpoint
    # Store it on the socket endpoint so handlers can access it
    trace_listener = TraceStreamListener(socketEndpoint)
    socketEndpoint.trace_listener = trace_listener
    logger.info(f"Initialized trace_listener and attached to SocketEndpoint: {trace_listener}")
    
    restEndpoint = endpoints.RestEndpoint(app, cors)

    loop=asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(init())
    loop.run_forever()
