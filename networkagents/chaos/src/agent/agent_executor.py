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
from agent.agent import ChaosAgent
from typing_extensions import override
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import (
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from google.genai import types
from a2a.utils import new_task, new_agent_text_message
from utils.error_handler import (
    ChaosAgentError,
    ErrorSeverity,
    create_error_status_event
)
from agent_library.trace.trace_context import TracingContext

logger = logging.getLogger(__name__)

class ChaosAgentExecutor(AgentExecutor):
    """Chaos AgentExecutor Example."""

    @override
    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """
        Handler for 'message/stream' requests.
        """

        logger.info("on execute")
        query = context.get_user_input()
        task = context.current_task

        # Set the trace ID to the A2A context_id for cross-agent correlation
        TracingContext.set_trace_id(context.context_id)

        # check message exists
        if not context.message:
            raise ChaosAgentError(
                message='No message provided',
                severity=ErrorSeverity.ERROR
            )

        # create a task if it doesnt exist
        if not task:
            logger.info("Creating new task!!")
            task = new_task(context.message)
            await event_queue.enqueue_event(task)

        logger.info("start stream %s, with id %s", query, task.context_id)
        try:
            agent = await ChaosAgent.get_instance()         

            session = await agent.session_service.get_session(app_name="ChaosAgent", user_id="agent", session_id=context.context_id)
            if session is None:
                logger.info("creating new session")
                session = await agent.session_service.create_session(
                    app_name="ChaosAgent",
                    user_id="agent",
                    session_id=context.context_id
                )

            content = types.Content(
                role='user', parts=[types.Part.from_text(text=query)]
            )

            async for event in agent.runner.run_async(user_id="agent", session_id=context.context_id, new_message=content):
                logger.info("ADK RUNNER EVENT")
                logger.info(event)

                if event.content.parts and event.content.parts[0].text:
                    logger.info(f'** {event.author}: {event.content.parts[0].text}')

                    await event_queue.enqueue_event(
                        TaskStatusUpdateEvent(
                            status=TaskStatus(
                                state=TaskState.completed,
                                message=new_agent_text_message(
                                    event.content.parts[0].text,
                                    task.context_id,
                                    task.id,
                                ),
                            ),
                            final=True,
                            context_id=task.context_id,
                            task_id=task.id,
                        )
                    )
                
        except ChaosAgentError as e:
            # If we have a task, report the error through the event queue
            if task:
                error_event = create_error_status_event(
                    error=e,
                    context_id=task.context_id,
                    task_id=task.id,
                    final=True
                )
                await event_queue.enqueue_event(error_event)
            # Re-raise the error
            raise
        except Exception as e:
            # Convert generic exceptions to TestAgentError and handle
            error = ChaosAgentError(
                message=f"Unexpected error in execute: {str(e)}",
                severity=ErrorSeverity.ERROR,
                original_exception=e
            )
            if task:
                error_event = create_error_status_event(
                    error=error,
                    context_id=task.context_id,
                    task_id=task.id,
                    final=True
                )
                await event_queue.enqueue_event(error_event)
            logger.error(f"Unexpected error in execute: {str(e)}", exc_info=True)
            # Re-raise the error
            raise error

    @override
    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        """
        Handler for cancel requests.
        """
        task = context.current_task
        
        try:
            # Attempt to get the agent instance
            agent = await ChaosAgent().get_instance()
            
            # Check if we have a valid task
            if not task:
                raise ChaosAgentError(
                    message='Cannot cancel: No active task found',
                    severity=ErrorSeverity.WARNING
                )
            
            # Report that cancellation is not supported but we're handling it gracefully
            logger.warning(f"Cancel requested for task {task.id}, but cancellation is not fully supported")
            
            # Send a status update to inform the user
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    status=TaskStatus(
                        state=TaskState.cancelled,
                        message=new_agent_text_message(
                            "Task cancellation requested. Note that some operations may continue in the background.",
                            task.context_id,
                            task.id,
                        ),
                    ),
                    final=True,
                    context_id=task.context_id,
                    task_id=task.id,
                )
            )
        except ChaosAgentError as e:
            # If we have a task, report the error through the event queue
            if task:
                error_event = create_error_status_event(
                    error=e,
                    context_id=task.context_id,
                    task_id=task.id,
                    final=True
                )
                await event_queue.enqueue_event(error_event)
            # Re-raise the error
            raise
        except Exception as e:
            # Convert generic exceptions to TestAgentError and handle
            error = ChaosAgentError(
                message=f"Unexpected error in cancel: {str(e)}",
                severity=ErrorSeverity.ERROR,
                original_exception=e
            )
            if task:
                error_event = create_error_status_event(
                    error=error,
                    context_id=task.context_id,
                    task_id=task.id,
                    final=True
                )
                await event_queue.enqueue_event(error_event)
            logger.error(f"Unexpected error in cancel: {str(e)}", exc_info=True)
            # Re-raise the error
            raise error
