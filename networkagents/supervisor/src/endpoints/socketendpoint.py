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
from agent.host_agent import HostAgent
from tools.topology import build_graph
from tools.logs import fetch_log_entries, delete_logs
from tools.metrics import *

logger = logging.getLogger(__name__)

# Dictionary to track clients that have requested logs
clients_state = {}
view_to_edge_label_map = {'network': 'isConnectedTo', 'resources': 'Manages', 'both': None}

class SocketEndpoint:
    """
    Socket.IO endpoint for handling client connections.
    """

    _instance = None

    def __init__(self, sio):
        logger.info("SocketEndpoint init")

        SocketEndpoint._instance = self

        self.sio = sio
        self.callbacks()


    async def sendPushNotification(self, data):
        """
        Send data to all connected clients

        Args:
            data: The data to send to all connected clients

        Returns:
            bool: True if the data was sent successfully, False otherwise
        """
        try:
            logger.info("Sending %s to all connected clients", data)
            await self.sio.emit('push_notification', data)
            return True
        except Exception as e:
            logger.error(f"Error sending data to all clients: {str(e)}", exc_info=True)
            return False


    def callbacks(self):
        @self.sio.event
        async def connect(sid, environ, auth):
            logger.info("connected client %s", sid)

            agent = await HostAgent.get_instance()
            agent.sio_sessions[sid] = self.sio
            logger.info(agent.sio_sessions)


        @self.sio.event
        async def chat_message(sid, data):
            """
            Handle plain text chat messages from the dashboard.

            Expected data format:
                { "text": "<user message>", "thread_id": "<session id>" }

            Responses are emitted as 'chat_response' events:
                { "text": "<chunk>", "done": false }   -- streaming chunk
                { "text": "",        "done": true  }   -- end of stream
            """
            logger.info("chat_message from %s: %s", sid, data)

            try:
                text = data.get('text', '') if isinstance(data, dict) else str(data)
                # Use thread_id if provided; fall back to the socket session id so
                # each browser tab gets its own conversation history.
                thread_id = data.get('thread_id', sid) if isinstance(data, dict) else sid

                if not text.strip():
                    logger.warning("Received empty chat_message from %s — ignoring", sid)
                    return

                agent = await HostAgent.get_instance()

                # Stream text chunks back to the client
                async for chunk in agent.run(thread_id, text):
                    await self.sio.emit('chat_response', {'text': chunk, 'done': False}, room=sid)

                # Signal end of stream
                await self.sio.emit('chat_response', {'text': '', 'done': True}, room=sid)

            except Exception as e:
                logger.error(f"Error processing chat_message from {sid}: {e}", exc_info=True)
                await self.sio.emit('chat_response', {
                    'text': f'Error: {str(e)}',
                    'done': True,
                    'error': True
                }, room=sid)


        @self.sio.event
        async def get_topology(sid, data):
            logger.info(f"get_topology for {sid}: {data}")
            try:
                if sid not in clients_state: clients_state[sid] = {}
                clients_state[sid]['topology'] = data

                edge_label = view_to_edge_label_map[data['view']]

                elements, success = build_graph(None, edge_label)
                
                if success:
                    response = {'elements': elements}
                    await self.sio.emit('topology_update', response, room=sid)
                    logger.info(f"Sent topology update to {sid} with {len(elements)} elements for '{data['view']}' view")
                else:
                    logger.error(f"Failed to build graph for client {sid}")
                    await self.sio.emit('chat_response', {'text': '[ERROR] Failed to build graph', 'done': True, 'error': True}, room=sid)
                    await self.sio.emit('topology_update', {'error': "Failed to build graph"}, room=sid)
            except Exception as e:
                logger.error(f"Error fetching topology: {e}")
                await self.sio.emit('chat_response', {'text': f'[ERROR] Error fetching topology: {str(e)}', 'done': True, 'error': True}, room=sid)
                await self.sio.emit('topology_update', {'error': f"Error fetching topology: {str(e)}"}, room=sid)
                
        @self.sio.event
        async def get_logs(sid, data):
            logger.info(f"get_logs for {sid}: {data}")
            try:                
                if sid not in clients_state: clients_state[sid] = {}
                clients_state[sid]['logs'] = data
                
                enabled = clients_state[sid]['logs']['enabled']
                if enabled:
                    logs = fetch_log_entries()
                    await self.sio.emit('logs_update', logs, room=sid)
                    logger.info(f"Sent initial logs to {sid}")
                
                logger.info(f"Logs {'enabled' if enabled else 'disabled'} for {sid}")
            except Exception as e:
                logger.error(f"Error handling get_logs: {e}")
                await self.sio.emit('logs_update', {'error': f"Error fetching logs: {str(e)}"}, room=sid)

        @self.sio.event
        async def get_traces(sid, data):
            logger.info(f"========== get_traces called for {sid}: {data} ==========")
            try:
                old_enabled = clients_state.get(sid, {}).get('traces', {}).get('enabled', False)
                logger.info(f"Old trace enabled state for {sid}: {old_enabled}")
                
                if sid not in clients_state: clients_state[sid] = {}
                clients_state[sid]['traces'] = data
                logger.info(f"Updated client state for {sid}: {clients_state[sid]}")
                
                enabled = data.get('enabled', False)
                logger.info(f"New trace enabled state: {enabled}")
                
                trace_listener = getattr(self, 'trace_listener', None)
                logger.info(f"trace_listener object: {trace_listener}")
                
                if enabled and not old_enabled:
                    logger.info("Client is ENABLING traces (transition from disabled to enabled)")
                    if trace_listener:
                        logger.info("Calling trace_listener.add_client()...")
                        await trace_listener.add_client()
                        logger.info("✓ trace_listener.add_client() completed")
                    else:
                        logger.error("❌ trace_listener is None!")
                    logger.info("✓ Trace streaming enabled - will receive new traces from current time forward")
                    
                elif not enabled and old_enabled:
                    logger.info("Client is DISABLING traces (transition from enabled to disabled)")
                    if trace_listener:
                        logger.info("Calling trace_listener.remove_client()...")
                        await trace_listener.remove_client()
                        logger.info("✓ trace_listener.remove_client() completed")
                else:
                    logger.info(f"No state transition (enabled={enabled}, old_enabled={old_enabled})")
                
                logger.info(f"========== Traces {'enabled' if enabled else 'disabled'} for {sid} ==========")
                
            except Exception as e:
                logger.error(f"❌ Error handling get_traces: {e}", exc_info=True)
                await self.sio.emit('traces_update', {'error': f"Error enabling traces: {str(e)}"}, room=sid)

        @self.sio.event
        async def reset_logs(sid):
            logger.info(f"reset_logs for {sid}")
            try:
                success = delete_logs()
                if success:
                    logs = []
                    await self.sio.emit('logs_update', logs, room=sid)
                    logger.info(f"Sent empty logs after reset to {sid}")
                else:
                    raise Exception("Logs deletion in database failed.")
            except Exception as e:
                logger.error(f"Error handling reset_logs: {e}")
                await self.sio.emit('logs_update', {'error': f"Error resetting logs: {str(e)}"}, room=sid)

        @self.sio.event
        async def reset_traces(sid, data):
            logger.info(f"========== reset_traces called for {sid}: {data} ==========")
            try:
                trace_listener = getattr(self, 'trace_listener', None)
                
                if trace_listener:
                    timestamp = data.get('timestamp')
                    logger.info(f"Resetting trace listener cursor to timestamp: {timestamp}")
                    await trace_listener.reset_cursor(timestamp=timestamp)
                    logger.info("✓ Trace cursor reset - new events will be fetched from the specified time")
                else:
                    logger.error("❌ trace_listener is None!")
                
                logger.info(f"========== Trace cursor reset for {sid} ==========")
                
            except Exception as e:
                logger.error(f"❌ Error handling reset_traces: {e}", exc_info=True)

        @self.sio.event
        async def disconnect(sid):
            logger.info("disconnected from %s", sid)

            if sid in clients_state:
                if clients_state[sid].get('traces', {}).get('enabled', False):
                    trace_listener = getattr(self, 'trace_listener', None)
                    if trace_listener:
                        await trace_listener.remove_client()
                        logger.info(f"Removed disconnected client {sid} from trace listener")
                
                del clients_state[sid]

            agent = await HostAgent.get_instance()
            if sid in agent.sio_sessions:
                del agent.sio_sessions[sid]
            logger.info(agent.sio_sessions)

        @self.sio.event
        async def get_all_last_metrics(sid):
            logger.info(f"get_all_last_metrics for {sid}")
            try:                
                metrics = fetch_all_last_metrics()
                await self.sio.emit('all_last_metrics_update', metrics, room=sid)
                logger.info(f"Sent all_last_metrics_update to {sid}")
            except Exception as e:
                logger.error(f"Error handling get_all_last_metrics: {e}")
                await self.sio.emit('all_last_metrics_update', {'error': f"Error fetching metrics: {str(e)}"}, room=sid)

        @self.sio.event
        async def get_all_metrics(sid):
            logger.info(f"get_all_metrics for {sid}")
            try:                
                metrics = fetch_all_metrics()
                await self.sio.emit('all_metrics_update', metrics, room=sid)
                logger.info(f"Sent all_metrics_update to {sid}")
            except Exception as e:
                logger.error(f"Error handling get_all_metrics: {e}")
                await self.sio.emit('all_metrics_update', {'error': f"Error fetching metrics: {str(e)}"}, room=sid)

        @self.sio.event
        async def get_last_metrics_for_id(sid, data):
            logger.info(f"get_last_metrics_for_id for {sid}: {data}")
            try:                
                metrics = fetch_last_metrics_for_id(data['id'])
                await self.sio.emit('last_metrics_update_for_id', metrics, room=sid)
                logger.info(f"Sent last_metrics_update_for_id logs to {sid}")
            except Exception as e:
                logger.error(f"Error handling get_last_metrics_for_id: {e}")
                await self.sio.emit('last_metrics_update_for_id', {'error': f"Error fetching metrics: {str(e)}"}, room=sid)

        @self.sio.event
        async def get_all_metrics_for_id(sid, data):
            logger.info(f"get_all_metrics_for_id for {sid}: {data}")
            try:                
                metrics = fetch_all_metrics_for_id(data['id'])
                await self.sio.emit('all_metrics_update_for_id', metrics, room=sid)
                logger.info(f"Sent all_metrics_update_for_id logs to {sid}")
            except Exception as e:
                logger.error(f"Error handling get_all_metrics_for_id: {e}")
                await self.sio.emit('all_metrics_update_for_id', {'error': f"Error fetching metrics: {str(e)}"}, room=sid)
                
        @self.sio.event
        async def reset_metrics(sid):
            logger.info(f"reset_metrics for {sid}")
            try:
                success = clear_network_metrics()
                if success:
                    await self.sio.emit('all_last_metrics_update', {}, room=sid)
                    await self.sio.emit('all_metrics_update', {}, room=sid)
                    logger.info(f"Sent empty metrics after reset to {sid}")
                else:
                    raise Exception("Metrics deletion in database failed.")
            except Exception as e:
                logger.error(f"Error handling reset_metrics: {e}")
                await self.sio.emit('all_metrics_update', {'error': f"Error resetting metrics: {str(e)}"}, room=sid)

