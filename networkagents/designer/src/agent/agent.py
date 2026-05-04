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

from google.adk.agents import SequentialAgent
from google.adk import Runner
from google.adk.artifacts import InMemoryArtifactService
from google.adk.sessions import InMemorySessionService
import logging
from agent_library.trace.trace_plugin import TracePlugin
from agent.subagents import approval_agent, deployment_agent, designer_agent, planner_agent, validate_agent

logger = logging.getLogger(__name__)

# Shared app name — both runners operate on the same session namespace
APP_NAME = "DesignerSupervisorAgent"


class DesignerAgent:
    """
    The designer agent.

    Uses two separate runners that share the same session service and app_name,
    so they operate on the same session (conversation history + state):

    - planner_runner: runs just PlannerSubAgent. Produces a NetworkChangePlan
      (structured JSON) stored in session state under 'change_plan'.
      The executor calls this repeatedly until the plan is approved.

    - execution_runner: runs the remaining pipeline after plan approval:
      DescriptorDesigner → Approver → Deployer → Validator.
      It reads 'change_plan' from session state via {change_plan} in prompts.
    """

    SUPPORTED_CONTENT_TYPES = ['text', 'text/plain']

    _instance = None

    @classmethod
    async def get_instance(cls):
        if DesignerAgent._instance is None:
            DesignerAgent._instance = cls()
        return DesignerAgent._instance

    def __init__(self):
        self.session_service = InMemorySessionService()
        self.artifact_service = InMemoryArtifactService()

        # Designer state machine: context_id → state string
        # Stored here (not in ADK session) because ADK's InMemorySessionService
        # reconstructs session.state from event history on each request, so
        # direct dict mutations are not persisted between turns.
        self._states: dict = {}           # context_id → _STATE_ASKING | _STATE_APPROVAL | None
        self._original_requests: dict = {}  # context_id → original user request text

        # Runner 1: planner only.
        # Runs to completion each turn; the executor inspects the output
        # and decides whether to pause for clarification, show the approval
        # widget, or proceed to execution.
        self.planner_runner = Runner(
            app_name=APP_NAME,
            agent=planner_agent,
            artifact_service=self.artifact_service,
            session_service=self.session_service,
            plugins=[TracePlugin()],
        )

        # Runner 2: execution pipeline (descriptor design → approval → deploy → validate).
        # Only called after the human has approved the plan.
        self.execution_agent = SequentialAgent(
            name="DesignerExecutionAgent",
            description=(
                "Execute an approved network change plan: generate CRD descriptors, "
                "validate them, deploy to the cluster, and verify all resources are Ready."
            ),
            sub_agents=[designer_agent]#, approval_agent, deployment_agent, validate_agent],
        )

        self.execution_runner = Runner(
            app_name=APP_NAME,
            agent=self.execution_agent,
            artifact_service=self.artifact_service,
            session_service=self.session_service,
            plugins=[TracePlugin()],
        )
