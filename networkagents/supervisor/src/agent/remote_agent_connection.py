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
        self.agent_client = None

        self.card = agent_card
        self.address = address
        self.agent_client = None

        self.conversation_name = None
        self.conversation = None
        self.pending_tasks = set()

    async def create_client(self):
        logger.info("creating client for %s with address %s", self.card.name, self.address)
        self.agent_client = A2AClient(httpx_client=httpx.AsyncClient(timeout=30.0), agent_card=self.card)
        # The discovered card address may be an internal address; override with the
        # external address so the client can actually reach the remote agent.
        self.agent_client.url = self.address

    def get_agent(self) -> AgentCard:
        return self.card

    async def send_message(self, sio_list, text):
        """
        Send a plain text progress update to all connected dashboard clients.

        Args:
            sio_list: dict of {sid: sio} for all active socket sessions
            text: the text to send
        """
        if text:
            for sid, sio in sio_list.items():
                await sio.emit('chat_response', {'text': text, 'done': False}, room=sid)

    async def send_task(self, request: SendMessageRequest):
        """
        Send a request/response (non-streaming) message to the remote agent.
        Used for background agent-to-agent interactions such as approval forwarding.
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
    ) -> tuple:
        """
        Send a streaming request to the remote agent — driven by chat interaction.
        Progress updates are forwarded to the dashboard via plain 'chat_response' socket events.

        Returns a tuple of (taskStatus, observed_task_id) so the caller can persist
        the task_id through ADK's tool_context.state (direct session.state mutations
        made via updateState() are lost when ADK rebuilds state from event history).
        """
        logger.info("REMOTE AGENT SEND STREAMING TASK")
        logger.info(request)
        logger.info(self.agent_client.url)

        # Track the task_id observed in the stream; returned to the caller so it
        # can be stored via tool_context.state (which creates an ADK event and
        # therefore survives the next runner turn).
        observed_task_id = None

        try:
            if self.card.capabilities.streaming:
                try:
                    async for chunk in self.agent_client.send_message_streaming(request):
                        logger.info("RESPONSE FROM AGENT")
                        logger.info(chunk)

                        if isinstance(chunk.root, SendStreamingMessageSuccessResponse) and isinstance(chunk.root.result, Task):
                            task: Task = chunk.root.result
                            observed_task_id = task.id
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
                                return taskStatus, observed_task_id

                            if taskStatus.state == TaskState.input_required:
                                logger.info("need info - returning task to supervisor to collect user input")
                                return taskStatus, observed_task_id
                            
                            if taskStatus.state == TaskState.working:
                                logger.info('working through steps.')
                                await self.send_message(sio_list, taskStatus.message.parts[0].root.text)

                            if taskStatus.state == TaskState.completed:
                                logger.info("Task is completed")
                                return taskStatus, observed_task_id

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
                raise

        # Stream exhausted without hitting a terminal state event.
        # Return the observed task_id so the caller can still persist it.
        return None, observed_task_id
