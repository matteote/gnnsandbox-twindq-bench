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
import datetime
import json
from uuid import uuid4
from agent.host_agent import HostAgent
from tools.topology import build_graph
from tools.logs import fetch_log_entries, delete_logs
from tools.metrics import *
from utils.error_handler import (
    SupervisorAgentError,
    ErrorSeverity,
    send_error_message
)
from ag_ui.core import RunAgentInput, UserMessage
from tools.agui import chartTool, approvalTool

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


    async def _convert_to_run_agent_input(self, data):
        """
        Convert incoming WebSocket data to AG-UI RunAgentInput.
        
        Args:
            data: Parsed JSON data from WebSocket
            
        Returns:
            RunAgentInput instance
        """
        # Handle simple text message format
        if isinstance(data, dict) and 'text' in data:
            # Simple text message - convert to AG-UI format
            thread_id = data.get('thread_id', str(uuid4()))
            run_id = data.get('run_id', str(uuid4()))
            
            user_message = UserMessage(
                id=str(uuid4()),
                content=data['text']
            )
            
            return RunAgentInput(
                thread_id=thread_id,
                run_id=run_id,
                state={},
                messages=[user_message],
                tools=[chartTool, approvalTool],
                context=[],
                forwardedProps={}
            )
        
        # Handle full AG-UI RunAgentInput format
        elif isinstance(data, dict) and 'thread_id' in data:
            return RunAgentInput(**data)
        
        else:
            raise ValueError(f"Invalid message format: {data}")

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
            # Emit a 'push_notification' event to all connected clients
            await self.sio.emit('push_notification', data)
            return True
        except Exception as e:
            logger.error(f"Error sending data to all clients: {str(e)}", exc_info=True)
            return False


    def callbacks(self):
        @self.sio.event
        async def connect(sid, environ, auth):
            logger.info("connected client %s", sid)

            # add sio to the agent
            agent=await HostAgent.get_instance()
            agent.sio_sessions[sid]=self.sio
            logger.info(agent.sio_sessions)


        @self.sio.event
        async def agui_message(sid, data):
            """
            Handle AG-UI protocol messages via Socket.IO
            
            Args:
                sid: Socket.IO session ID
                data: AG-UI message data
            """
            logger.info("AG-UI message from %s: %s", sid, data)
            
            try:
                # Convert to RunAgentInput
                run_input = await self._convert_to_run_agent_input(data)
                logger.info(f"SocketEndpoint: Converted to RunAgentInput - thread_id: {run_input.thread_id}, run_id: {run_input.run_id}")
                
                # Get agent instance and run AG-UI protocol
                agent = await HostAgent.get_instance()
                
                # Stream AG-UI events back to client via Socket.IO
                async for event in agent.run_agui(run_input):
                    event_data = event.model_dump()
                    await self.sio.emit('agui_event', event_data, room=sid)
                    logger.debug(f"SocketEndpoint: Emitted {type(event).__name__} to session {sid}")
                    
            except Exception as e:
                logger.error(f"Error processing AG-UI message from {sid}: {e}", exc_info=True)
                error_response = {
                    "type": "RUN_ERROR",
                    "message": f"Error processing AG-UI message: {str(e)}",
                    "code": "AGUI_PROCESSING_ERROR"
                }
                await self.sio.emit('agui_event', error_response, room=sid)

        @self.sio.event
        async def get_topology(sid, data):
            logger.info(f"get_topology for {sid}: {data}")
            try:
                # Update the client topology preferences
                if sid not in clients_state: clients_state[sid] = {}
                clients_state[sid]['topology'] = data

                # map dashboard view dropdown menu entries to graph labels
                edge_label = view_to_edge_label_map[data['view']]

                # Build the graph with selected edge label.
                # build_graph uses the module-level _database singleton internally;
                # the database argument is kept for signature compatibility only.
                elements, success = build_graph(None, edge_label)
                
                if success:
                    # Prepare response
                    response = {'elements': elements}
                    
                    # Send topology update to the client
                    await self.sio.emit('topology_update', response, room=sid)
                    logger.info(f"Sent topology update to {sid} with {len(elements)} elements for '{data['view']}' view")
                else:
                    logger.error(f"Failed to build graph for client {sid}")
                    error = SupervisorAgentError(
                        message="Failed to build graph",
                        severity=ErrorSeverity.ERROR
                    )
                    await send_error_message(self.sio, sid, error)
                    await self.sio.emit('topology_update', {'error': "Failed to build graph"}, room=sid)
            except Exception as e:
                logger.error(f"Error fetching topology: {e}")
                error = SupervisorAgentError(
                    message=f"Error fetching topology: {str(e)}",
                    severity=ErrorSeverity.ERROR,
                    original_exception=e
                )
                await send_error_message(self.sio, sid, error)
                await self.sio.emit('topology_update', {'error': f"Error fetching topology: {str(e)}"}, room=sid)
                
        @self.sio.event
        async def get_logs(sid, data):
            logger.info(f"get_logs for {sid}: {data}")
            try:                
                # Update the client's log preference
                if sid not in clients_state: clients_state[sid] = {}
                clients_state[sid]['logs'] = data
                
                enabled = clients_state[sid]['logs']['enabled']
                if enabled:
                    # Fetch and send initial logs
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
                # Get current state to detect transitions
                old_enabled = clients_state.get(sid, {}).get('traces', {}).get('enabled', False)
                logger.info(f"Old trace enabled state for {sid}: {old_enabled}")
                
                # Update the client's trace preference
                if sid not in clients_state: clients_state[sid] = {}
                clients_state[sid]['traces'] = data
                logger.info(f"Updated client state for {sid}: {clients_state[sid]}")
                
                enabled = data.get('enabled', False)
                logger.info(f"New trace enabled state: {enabled}")
                
                # Access trace_listener from self (attached in main.py)
                trace_listener = getattr(self, 'trace_listener', None)
                logger.info(f"trace_listener object: {trace_listener}")
                
                # Manage listener based on client count
                if enabled and not old_enabled:
                    # Client enabled traces
                    logger.info("Client is ENABLING traces (transition from disabled to enabled)")
                    if trace_listener:
                        logger.info("Calling trace_listener.add_client()...")
                        await trace_listener.add_client()
                        logger.info("✓ trace_listener.add_client() completed")
                    else:
                        logger.error("❌ trace_listener is None!")
                    
                    # Don't fetch historical traces - only send new traces from now on
                    logger.info("✓ Trace streaming enabled - will receive new traces from current time forward")
                    
                elif not enabled and old_enabled:
                    # Client disabled traces
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
                # Access trace_listener from self (attached in main.py)
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

        # reset_chat handler removed - AG-UI chat panel manages thread IDs directly
            
        @self.sio.event
        async def disconnect(sid):
            logger.info("disconnected from %s", sid)

            # Check if this client had traces enabled
            if sid in clients_state:
                if clients_state[sid].get('traces', {}).get('enabled', False):
                    # Access trace_listener from self (attached in main.py)
                    trace_listener = getattr(self, 'trace_listener', None)
                    if trace_listener:
                        await trace_listener.remove_client()
                        logger.info(f"Removed disconnected client {sid} from trace listener")
                
                del clients_state[sid]

            # remove the sid/sio from the agent session
            agent=await HostAgent.get_instance()
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
                    # Send empty metrics updates to the client
                    await self.sio.emit('all_last_metrics_update', {}, room=sid)
                    await self.sio.emit('all_metrics_update', {}, room=sid)
                    logger.info(f"Sent empty metrics after reset to {sid}")
                else:
                    raise Exception("Metrics deletion in database failed.")
            except Exception as e:
                logger.error(f"Error handling reset_metrics: {e}")
                await self.sio.emit('all_metrics_update', {'error': f"Error resetting metrics: {str(e)}"}, room=sid)
                
        @self.sio.event
        async def agui_tool_result(sid, data):
            """
            Handle AG-UI tool results from the dashboard UI
            
            Args:
                sid: The session ID of the client
                data: The tool result data containing tool_call_id and content
            """
            try:
                tool_call_id = data.get('tool_call_id')
                content = data.get('content')
                
                logger.info(f"Received AG-UI tool result from {sid}: {tool_call_id} -> {content}")
                
                # Extract thread_id from the encoded tool_call_id if present
                # Format: original_id::thread_id
                session_id_to_use = sid  # Default to socket session ID
                
                if tool_call_id and "::" in tool_call_id:
                    # Extract thread_id from encoded tool_call_id
                    parts = tool_call_id.split("::", 1)
                    if len(parts) == 2:
                        original_tool_call_id, thread_id = parts
                        session_id_to_use = thread_id
                        logger.info(f"Extracted thread_id {thread_id} from encoded tool_call_id {tool_call_id}")
                    else:
                        logger.warning(f"Invalid encoded tool_call_id format: {tool_call_id}")
                else:
                    logger.info(f"Using socket session ID {sid} as fallback for tool_call_id {tool_call_id}")
                
                # Get agent instance and forward the tool result with the correct session ID
                agent = await HostAgent.get_instance()
                await agent.handleToolResult(session_id_to_use, tool_call_id, content)
                
            except Exception as e:
                logger.error(f"Error handling AG-UI tool result: {e}")

        @self.sio.event
        async def notification_feedback(sid, data):
            """
            Handle notification feedback from the dashboard UI (thumbs up/down)
            
            Args:
                sid: The session ID of the client
                data: The feedback data containing notification details and feedback type
            """
            try:
                notification_id = data.get('notification_id')
                feedback = data.get('feedback')  # 'approve' or 'reject'
                notification_details = data.get('notification_details', {})
                
                # Log the feedback event
                logger.info(f"Received notification feedback from {sid}: {feedback} for notification {notification_id}")
                logger.info(f"Notification details: {notification_details}")
                
                agent = await HostAgent.get_instance()
                # send the approval
                await agent.sendApproval(notification_details['name'],feedback, notification_details['task_id'], notification_details['context_id'])

            except Exception as e:
                logger.error(f"Error handling notification feedback: {e}")
