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

import httpx
import logging
import datetime
from a2a.client import A2AClient
from a2a.types import (
    AgentCard,
    SendStreamingMessageRequest,
    SendStreamingMessageSuccessResponse,
    SendMessageRequest,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatusUpdateEvent,
    Task,
)
from utils.error_handler import (
    SupervisorAgentError,
    RemoteAgentError,
    ErrorSeverity,
    send_error_message
)

logger = logging.getLogger(__name__)

class RemoteAgentConnections:
    """A class to hold the connections to the remote agents."""

    def __init__(self, host_agent, agent_card: AgentCard, address: str):
        self.host_agent = host_agent
        self.agent_client =None

        self.card = agent_card
        self.address = address
        self.agent_client=None

        self.conversation_name = None
        self.conversation = None
        self.pending_tasks = set()

    async def create_client(self):
        logger.info("creating client for %s with address %s", self.card.name, self.address)
        self.agent_client = A2AClient(httpx_client=httpx.AsyncClient(timeout=30.0), agent_card=self.card)
        # the discovered card address is the internal address of the server, make sure to update with the external address or is not reachable
        self.agent_client.url=self.address

    def get_agent(self) -> AgentCard:
        return self.card

    async def send_message(self, sio_list, text):
        """
        Utility function to send AG-UI events back to the dashboard ui
        """
        if text != '':
            # Generate a unique message ID for this progress update
            import uuid
            message_id = str(uuid.uuid4())
            
            # Send proper AG-UI events: START -> CONTENT -> END
            start_event = {
                'type': 'TEXT_MESSAGE_START',
                'timestamp': None,
                'raw_event': None,
                'message_id': message_id,
                'role': 'assistant'
            }
            
            content_event = {
                'type': 'TEXT_MESSAGE_CONTENT',
                'timestamp': None,
                'raw_event': None,
                'message_id': message_id,
                'delta': text
            }
            
            end_event = {
                'type': 'TEXT_MESSAGE_END',
                'timestamp': None,
                'raw_event': None,
                'message_id': message_id
            }
            
            # Send AG-UI events to all registered sessions
            for sid, sio in sio_list.items():
                await sio.emit('agui_event', start_event, room=sid)
                await sio.emit('agui_event', content_event, room=sid)
                await sio.emit('agui_event', end_event, room=sid)

    async def send_task(self, request: SendMessageRequest):
        """
        Send a request/response request to the agent - driven by background agent to agent interaction
        """
        logger.info("REMOTE AGENT SEND TASK")
        logger.info(request)
        logger.info(self.agent_client.url)

        try:

            result = await self.agent_client.send_message(request)
            return result

        except httpx.HTTPError as e:
            logger.error(f"HTTP error communicating with remote agent: {str(e)}", exc_info=True)
        except Exception as e:
            logger.error(f"Error communicating with remote agent: {str(e)}", exc_info=True)

    async def send_streaming_task(
        self,
        request: SendStreamingMessageRequest,
        session_id, 
        sio_list
    ) -> Task | None:
        """
        Send a streaming request to the agent - driven by chat interaction
        """
        logger.info("REMOTE AGENT SEND STREAMING TASK")
        logger.info(request)
        logger.info(self.agent_client.url)

        try:
            if self.card.capabilities.streaming:
                try:
                    async for chunk in self.agent_client.send_message_streaming(request):
                        logger.info("RESPONSE FROM AGENT")
                        logger.info(chunk)

                        if isinstance(chunk.root, SendStreamingMessageSuccessResponse) and isinstance(chunk.root.result, Task):
                            task: Task = chunk.root.result
                            # Update the session state with the task ID and agent name for the current session
                            await self.host_agent.updateState(
                                session_id=session_id, 
                                agent_name=self.card.name,
                                task_status="submitted",
                                task_id=task.id
                            )
                            logger.info("updated task with id %s for agent %s in session %s", task.id, self.card.name, session_id)

                        if isinstance(chunk.root, SendStreamingMessageSuccessResponse) and isinstance(chunk.root.result, TaskArtifactUpdateEvent):
                            taskEvent: TaskArtifactUpdateEvent = chunk.root.result.artifact
                            logger.info(taskEvent)
                            await self.send_message(sio_list, taskEvent.parts[0].root.text)

                        if isinstance(chunk.root, SendStreamingMessageSuccessResponse) and isinstance(chunk.root.result, TaskStatusUpdateEvent):
                            taskStatus: TaskStatusUpdateEvent = chunk.root.result.status

                            # Check if there's an error in the task status
                            if taskStatus.state == TaskState.failed:
                                error_message = "Task failed"
                                if taskStatus.message and taskStatus.message.parts and taskStatus.message.parts[0].root.text:
                                    error_message = taskStatus.message.parts[0].root.text
                                
                                error = RemoteAgentError(
                                    message=f"Remote agent task failed: {error_message}",
                                    agent_name=self.card.name,
                                    severity=ErrorSeverity.ERROR
                                )
                                await send_error_message(sio_list, error)
                                return taskStatus

                            # if input is required then return the task status message
                            if taskStatus.state == TaskState.input_required:
                                logger.info("need info - returning task to supervisor to collect user input")
                                return taskStatus
                            
                            if taskStatus.state == TaskState.working:
                                logger.info('working through steps.')
                                # don't return to the model - just send update to socket so user see'sd progress in chat
                                await self.send_message(sio_list, taskStatus.message.parts[0].root.text)

                            if taskStatus.state == TaskState.completed:
                                # task is finished so get supervisor agent to summarise
                                logger.info("Task is completed")
                                return taskStatus

                except httpx.HTTPError as e:
                    error = RemoteAgentError(
                        message=f"HTTP error communicating with remote agent: {str(e)}",
                        agent_name=self.card.name,
                        severity=ErrorSeverity.ERROR,
                        original_exception=e
                    )
                    await send_error_message(sio_list, error)
                    logger.error(f"HTTP error communicating with remote agent: {str(e)}", exc_info=True)
                    raise error
                except Exception as e:
                    error = RemoteAgentError(
                        message=f"Error communicating with remote agent: {str(e)}",
                        agent_name=self.card.name,
                        severity=ErrorSeverity.ERROR,
                        original_exception=e
                    )
                    await send_error_message(sio_list, error)
                    logger.error(f"Error communicating with remote agent: {str(e)}", exc_info=True)
                    raise error
            else:
                error = RemoteAgentError(
                    message=f"Remote agent {self.card.name} does not support streaming",
                    agent_name=self.card.name,
                    severity=ErrorSeverity.ERROR
                )
                await send_error_message(sio_list, error)
                logger.error(f"Remote agent {self.card.name} does not support streaming")
                raise error
        except Exception as e:
            if not isinstance(e, SupervisorAgentError):
                error = RemoteAgentError(
                    message=f"Unexpected error in send_task: {str(e)}",
                    agent_name=self.card.name,
                    severity=ErrorSeverity.ERROR,
                    original_exception=e
                )
                await send_error_message(sio_list, error)
                logger.error(f"Unexpected error in send_task: {str(e)}", exc_info=True)
                raise error
            else:
                # Re-raise SupervisorAgentError instances
                raise
