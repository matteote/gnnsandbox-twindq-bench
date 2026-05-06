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
from uuid import uuid4
import json
from typing import Any, AsyncGenerator
import logging
import os
import agent.prompts.supervisor as prompts
from agent_library.credentials.creds import get_credentials
import datetime
from a2a.client import A2ACardResolver
from a2a.types import (
    AgentCard,
    SendStreamingMessageRequest,
    TaskState,
    MessageSendParams,
)
from google.adk import Agent, Runner
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.tool_context import ToolContext
from google.adk.sessions import InMemorySessionService
from google.adk.agents import RunConfig
from google.adk.agents.run_config import StreamingMode
from google.genai import types
from utils.error_handler import (
    SupervisorAgentError,
    RemoteAgentError,
    ErrorSeverity,
    send_error_message
)

from .remote_agent_connection import RemoteAgentConnections

logger = logging.getLogger(__name__)

class HostAgent:
    """
    The host agent.

    This is the agent responsible for choosing which remote agents to send
    tasks to and coordinate their work.
    """

    # static agent instance
    _instance = None

    @classmethod
    async def get_instance(cls):
        if HostAgent._instance is None:
            HostAgent._instance = cls()
            await HostAgent._instance.load_remote_agents()
        return HostAgent._instance

    def __init__(self):
        """
        Init agent and runner
        """
        self.credentials, self.projectid = get_credentials()

        self.app_name = "host_network_agent"

        self.remote_agent_addresses = []
        # Check if AGENTS_URL environment variable exists and parse it
        agents_url = os.environ.get('AGENTS_URL')
        if agents_url:
            self.remote_agent_addresses = [url.strip() for url in agents_url.split(',')]

        self.cards = {}

        # dict with sid->sio for sending messages back to dashboard socket session
        self.sio_sessions = {}

        # ADK session service and runner 
        self.session_service = InMemorySessionService()
        self.host_agent = self.create_agent()
        self.runner = Runner(
            app_name=self.app_name,
            agent=self.host_agent,
            session_service=self.session_service,
        )

        # list of loaded remote agents
        self.agents = None


    async def load_remote_agents(self):
        """
        (Re)Load the list of agent urls
        """
        try:
            # Close any existing connections
            if hasattr(self, 'remote_agent_connections') and self.remote_agent_connections:
                for connection in self.remote_agent_connections.values():
                    # Close any httpx connections
                    if hasattr(connection, 'client') and connection.client:
                        await connection.client.aclose()
                        
            # Initialize new connections
            self.remote_agent_connections: dict[str, RemoteAgentConnections] = {}
            self.cards: dict[str, AgentCard] = {}

            for address in self.remote_agent_addresses:
                try:
                    async with httpx.AsyncClient() as httpx_client:
                        try:
                            card_resolver = A2ACardResolver(httpx_client=httpx_client, base_url=address)
                            card = await card_resolver.get_agent_card()
                            card.url = address
                            logger.info("CARD INFO----------------")
                            logger.info(card)
                            remote_connection = RemoteAgentConnections(self, card, address)
                            await remote_connection.create_client()
                            self.remote_agent_connections[card.name] = remote_connection
                            self.cards[card.name] = card

                        except httpx.HTTPError as e:
                            logger.error(f"HTTP error loading remote agent at {address}: {str(e)}", exc_info=True)
                            continue
                        except Exception as e:
                            logger.error(f"Error loading remote agent at {address}: {str(e)}", exc_info=True)
                            continue
                except Exception as e:
                    logger.error(f"Unexpected error loading remote agent at {address}: {str(e)}", exc_info=True)
                    continue

            agent_info = []
            for ra in self.list_remote_agents():
                agent_info.append(json.dumps(ra))

            self.agents = '\n'.join(agent_info)
        except Exception as e:
            logger.error(f"Error loading remote agents: {str(e)}", exc_info=True)
            raise SupervisorAgentError(
                message=f"Error loading remote agents: {str(e)}",
                severity=ErrorSeverity.ERROR,
                original_exception=e
            )

    def create_agent(self) -> Agent:
        """
        Create ADK Host Agent

        Returns:
            Gemini agent with list of remote agents and task to route
        """
        return Agent(
            model='gemini-2.5-flash',
            name=self.app_name,
            instruction=self.root_instruction,
            description=(
                'This agent orchestrates the decomposition of the user request into'
                'tasks that can be performed by the child agents.'
            ),
            tools=[
                self.list_remote_agents,
                self.send_task,
            ],
        )

    def root_instruction(self, context: ReadonlyContext) -> str:
        """
        Build the root instruction for the host agent
        """
        current_agent = self.check_state(context)
        return prompts.supervisor_prompt.format(
            agents=self.agents,
            current_agent=current_agent['active_agent'],
            current_time=datetime.datetime.now().isoformat(),
            current_task_status=current_agent['task_status']
        )

    def check_state(self, context: ReadonlyContext):
        state = context.state
        returnObj = {
            'active_agent': 'Supervisor',
            'task_status': None
        }
        if 'agent' in state:
            returnObj['active_agent'] = f'{state["agent"]}'
        if 'task_status' in state:
            returnObj['task_status'] = f'{state["task_status"]}'

        return returnObj

    async def get_or_create_session(self, session_id: str):
        """
        Retrieve an existing ADK session or create a new one.

        Args:
            session_id: The session / thread ID (from the socket client)

        Returns:
            Tuple of (session, user_id)
        """
        user_id = f"user_{session_id}"
        session = await self.session_service.get_session(
            app_name=self.app_name,
            user_id=user_id,
            session_id=session_id
        )
        if session is None:
            session = await self.session_service.create_session(
                app_name=self.app_name,
                user_id=user_id,
                session_id=session_id,
                state={'session_id': session_id}
            )
            logger.info(f"Created new session {session_id} for user {user_id}")
        return session, user_id

    async def run(self, session_id: str, text: str) -> AsyncGenerator[str, None]:
        """
        Run the host agent for a user message and stream text responses.

        Args:
            session_id: The session / thread ID (from the socket client)
            text: The user message text

        Yields:
            Text chunks from the agent response
        """
        logger.info("run: session_id=%s, text=%s", session_id, text[:100])

        _, user_id = await self.get_or_create_session(session_id)

        new_message = types.Content(
            role='user',
            parts=[types.Part(text=text)]
        )

        run_config = RunConfig(streaming_mode=StreamingMode.SSE)

        emitted_any = False
        try:
            async for event in self.runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=new_message,
                run_config=run_config
            ):
                logger.debug("ADK event: is_final=%s, has_content=%s", event.is_final_response(), bool(event.content))

                # Emit partial (streaming) text events; skip the duplicated final aggregate
                if event.content and event.content.parts and not event.is_final_response():
                    for part in event.content.parts:
                        if hasattr(part, 'text') and part.text:
                            emitted_any = True
                            yield part.text

            # If no partial events arrived (e.g. all-or-nothing model response),
            # re-run without streaming to get the final text.
            if not emitted_any:
                logger.debug("No partial events received; re-running without streaming for final text")
                async for event in self.runner.run_async(
                    user_id=user_id,
                    session_id=session_id,
                    new_message=None,  # session already has the message
                    run_config=RunConfig(streaming_mode=StreamingMode.NONE)
                ):
                    if event.is_final_response() and event.content and event.content.parts:
                        for part in event.content.parts:
                            if hasattr(part, 'text') and part.text:
                                yield part.text

        except Exception as e:
            logger.error(f"Error running agent for session {session_id}: {e}", exc_info=True)
            raise

    async def add_remote_agent(self, agent_url: str):
        """
        Add a new remote agent to the list

        Args:
            agent_url: valid url for the remote agent
        Returns:
            dict with description, name and url
        """
        logger.info("adding agent %s", agent_url)

        try:
            self.remote_agent_addresses.append(agent_url)
            response = {}
            async with httpx.AsyncClient() as httpx_client:
                try:
                    card_resolver = A2ACardResolver(httpx_client=httpx_client, base_url=agent_url)
                    card = await card_resolver.get_agent_card()
                    response['id'] = str(uuid4())
                    response['name'] = card.name
                    response['description'] = card.description
                    response['url'] = agent_url
                
                    await self.load_remote_agents()

                    return response
                except httpx.HTTPError as e:
                    if agent_url in self.remote_agent_addresses:
                        self.remote_agent_addresses.remove(agent_url)
                    
                    logger.error(f"HTTP error adding remote agent at {agent_url}: {str(e)}", exc_info=True)
                    raise RemoteAgentError(
                        message=f"HTTP error adding remote agent: {str(e)}",
                        agent_name=agent_url,
                        severity=ErrorSeverity.ERROR,
                        original_exception=e
                    )
                except Exception as e:
                    if agent_url in self.remote_agent_addresses:
                        self.remote_agent_addresses.remove(agent_url)
                    
                    logger.error(f"Error adding remote agent at {agent_url}: {str(e)}", exc_info=True)
                    raise RemoteAgentError(
                        message=f"Error adding remote agent: {str(e)}",
                        agent_name=agent_url,
                        severity=ErrorSeverity.ERROR,
                        original_exception=e
                    )
        except SupervisorAgentError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error adding remote agent: {str(e)}", exc_info=True)
            raise SupervisorAgentError(
                message=f"Unexpected error adding remote agent: {str(e)}",
                severity=ErrorSeverity.ERROR,
                original_exception=e
            )

        return None
        
    async def delete_remote_agent(self, agent_url: str):
        """
        Delete a remote agent from the list

        Args:
            agent_url: url of the remote agent to delete
        """
        logger.info("deleting agent %s", agent_url)
        
        if agent_url in self.remote_agent_addresses:
            self.remote_agent_addresses.remove(agent_url)
            await self.load_remote_agents()
            return True
        else:
            logger.warning("Agent URL %s not found in remote_agent_addresses", agent_url)
            return False


    def list_remote_agents(self):
        """
        List the available remote agents with chat skills you can use to delegate chat tasks.

        Returns:
            list of dicts
        """
        if not self.remote_agent_connections:
            return []

        remote_agent_info = []
        for card in self.cards.values():
            for skill in card.skills:
                if 'chat' in skill.tags:
                    agent_id = str(uuid4())
                    remote_agent_info.append(
                        {'id': agent_id, 'name': card.name, 'description': card.description, 'url': card.url}
                    )
                break
        return remote_agent_info

    def list_all_remote_agents(self):
        """
        List all the available remote agents.

        Returns:
            list of dicts
        """
        if not self.remote_agent_connections:
            return []

        remote_agent_info = []
        for card in self.cards.values():
            for skill in card.skills:
                agent_id = str(uuid4())
                remote_agent_info.append(
                    {'id': agent_id, 'name': card.name, 'description': card.description, 'url': card.url}
                )
                break
        return remote_agent_info

    def create_send_message_payload(self, text: str, task_id: str | None = None, context_id: str | None = None) -> dict[str, Any]:
        """Helper function to create the payload for sending a task."""

        logger.info("create request with %s, task_id %s, context_id %s", text, task_id, context_id)

        payload: dict[str, Any] = {
            'message': {
                'role': 'user',
                'parts': [{'kind': 'text', 'text': text}],
                'messageId': uuid4().hex,
            },
        }
        if task_id is not None:
            payload['message']['taskId'] = task_id

        if context_id:
            payload['message']['contextId'] = context_id

        return payload


    async def updateState(self, session_id: str, agent_name: str | None = None, task_status: str | None = None, task_id: str | None = None):
        """
        Update the state of the current task/session directly via the session service.
        """
        logger.info("Update state for session %s: agent=%s, status=%s, task_id=%s",
                    session_id, agent_name, task_status, task_id)

        user_id = f"user_{session_id}"
        try:
            session = await self.session_service.get_session(
                app_name=self.app_name,
                user_id=user_id,
                session_id=session_id
            )
            if session is None:
                logger.warning(f"Session {session_id} not found for state update — skipping")
                return

            # InMemorySessionService: mutate state dict directly (it's an in-memory reference)
            if agent_name is not None:
                session.state['agent'] = agent_name
            if task_status is not None:
                session.state['task_status'] = task_status
            if task_id is not None:
                session.state['task_id'] = task_id

            logger.info(f"Successfully updated state for session {session_id}")

        except Exception as e:
            logger.error(f"Error updating state for session {session_id}: {e}", exc_info=True)


    async def send_task(self, agent_name: str, message: str, tool_context: ToolContext):
        """
        Sends a task either streaming (if supported) or non-streaming.

        This will send a message to the remote agent named agent_name.

        Args:
          agent_name: The name of the agent to send the task to.
          message: The message to send to the agent for the task.
          tool_context: The tool context this method runs in.

        Returns:
          A dictionary of JSON data from the agent.
        """

        logger.info("send task %s to %s", message, agent_name)

        try:
            state = tool_context.state
            state['agent'] = agent_name
            session_id = state.get('session_id')

            if agent_name not in self.remote_agent_connections:
                error = RemoteAgentError(
                    message=f"Agent {agent_name} not found",
                    agent_name=agent_name,
                    severity=ErrorSeverity.ERROR
                )
                
                if session_id and session_id in self.sio_sessions:
                    await send_error_message(self.sio_sessions, error)
                
                return {
                    "status": "Task Error",
                    "text": f'Agent {agent_name} not found'
                }

            client = self.remote_agent_connections[agent_name]
            if not client:
                error = RemoteAgentError(
                    message=f'Client not available for {agent_name}',
                    agent_name=agent_name,
                    severity=ErrorSeverity.ERROR
                )
                
                if session_id and session_id in self.sio_sessions:
                    await send_error_message(self.sio_sessions, error)
                
                return {
                    "status": "Task Error",
                    "text": f'Client not available for {agent_name}'
                }

            task_id = None
            if state.get('task_id') and state.get('task_id') != 'None':
                task_id = state['task_id']

            state['task_status'] = "running"

            send_payload = self.create_send_message_payload(
                text=message,
                context_id=session_id,
                task_id=task_id
            )
            
            request = SendStreamingMessageRequest(
                id=str(uuid4()),
                params=MessageSendParams(**send_payload)
            )

            try:
                taskStatus, new_task_id = await client.send_streaming_task(request, session_id, self.sio_sessions)
                logger.info("TASK STATUS FROM CLIENT SEND")
                logger.info(taskStatus)

                # Persist task_id through tool_context.state so ADK records it as an
                # event.  Direct mutations via updateState() bypass ADK's event system
                # and are overwritten when ADK reconstructs session.state from event
                # history on the next runner turn — causing the designer to receive a
                # message with no taskId, create a fresh task, and restart the planner.
                if new_task_id:
                    state['task_id'] = new_task_id
                    logger.info("Persisted task_id %s via tool_context.state", new_task_id)

                if taskStatus is not None:
                    if taskStatus.state == TaskState.input_required:
                        await self.updateState(session_id=session_id, task_status="input_needed")
                        return {
                            "status": "Input Required from User",
                            "text": taskStatus.message.parts[0].root.text,
                            "require_user_input": True,
                        }
                    elif taskStatus.state == TaskState.completed:
                        # Reset task_id through tool_context.state so subsequent
                        # send_task calls start a fresh task on the designer side.
                        state['task_id'] = 'None'
                        await self.updateState(session_id=session_id, agent_name="None", task_status="None", task_id="None")
                        return {
                            "status": "Task Completed",
                            "text": taskStatus.message.parts[0].root.text
                        }
                    elif taskStatus.state == TaskState.failed:
                        state['task_id'] = 'None'
                        await self.updateState(session_id=session_id, task_status="failed")
                        error_message = "Task failed"
                        if taskStatus.message and taskStatus.message.parts and taskStatus.message.parts[0].root.text:
                            error_message = taskStatus.message.parts[0].root.text
                        
                        return {
                            "status": "Task Failed",
                            "text": error_message
                        }
                    else:
                        logger.error("Unexpected task state found")
                        return {
                            "status": "Task Error",
                            "text": f"Unexpected task state: {taskStatus.state}"
                        }
                else:
                    return {
                        "status": "Task Completed",
                        "text": "Completed"
                    }
            except SupervisorAgentError as e:
                return {
                    "status": "Task Error",
                    "text": e.message
                }
        except Exception as e:
            logger.error(f"Unexpected error in send_task: {str(e)}", exc_info=True)
            error = SupervisorAgentError(
                message=f"Unexpected error in send_task: {str(e)}",
                severity=ErrorSeverity.ERROR,
                original_exception=e
            )
            
            try:
                state = tool_context.state
                session_id = state.get('session_id')
                
                if session_id and session_id in self.sio_sessions:
                    await send_error_message(self.sio_sessions, error)
            except Exception:
                pass
            
            return {
                "status": "Task Error",
                "text": f"Unexpected error: {str(e)}"
            }
