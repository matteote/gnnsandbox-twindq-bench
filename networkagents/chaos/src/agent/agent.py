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
from typing import ClassVar, Optional

from google.adk.agents import LlmAgent
from google.adk import Runner
from google.adk.artifacts import InMemoryArtifactService
from google.adk.sessions import InMemorySessionService

from agent.prompts import root_prompt
from agent_library.trace.trace_plugin import TracePlugin
from descriptions import description
from tools.docs import getFailureAnalysis
from tools.faults import deleteFault, deploySpec, getDeployedFaults, getFaultDescriptors

logger = logging.getLogger(__name__)

# Application name shared across all runners operating on the same session namespace.
APP_NAME = "ChaosAgent"

# Model used by the root LLM agent.
_MODEL = "gemini-2.5-flash"


class ChaosAgent:
    """Singleton wrapper around the ADK LlmAgent and Runner for chaos/fault injection.

    This class owns the session and artifact services, the root LlmAgent, and the
    ADK Runner.  A single shared instance is created on first access via
    :meth:`get_instance` and reused for the lifetime of the process, which keeps
    the in-memory session store consistent across concurrent requests.

    Attributes:
        SUPPORTED_CONTENT_TYPES: Content types accepted and produced by this agent.
        session_service: In-memory ADK session store.
        artifact_service: In-memory ADK artifact store.
        root_agent: The underlying :class:`~google.adk.agents.LlmAgent`.
        runner: The ADK :class:`~google.adk.Runner` that drives the agent.
    """

    SUPPORTED_CONTENT_TYPES: ClassVar[list[str]] = ["text", "text/plain"]

    _instance: ClassVar[Optional["ChaosAgent"]] = None

    # ------------------------------------------------------------------
    # Singleton access
    # ------------------------------------------------------------------

    @classmethod
    async def get_instance(cls) -> "ChaosAgent":
        """Return the shared :class:`ChaosAgent` instance, creating it if needed.

        The instance is created lazily on the first call.  Subsequent calls
        return the same object without re-initialising services or the runner.

        Returns:
            The singleton :class:`ChaosAgent` instance.
        """
        if cls._instance is None:
            logger.info("Initialising ChaosAgent singleton")
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def __init__(self) -> None:
        """Initialise services, the LLM agent, and the ADK runner.

        Do not call this directly in application code — use
        :meth:`get_instance` instead to ensure only one instance exists.
        """
        self.session_service = InMemorySessionService()
        self.artifact_service = InMemoryArtifactService()

        self.root_agent = LlmAgent(
            name=APP_NAME,
            description=description,
            model=_MODEL,
            instruction=root_prompt,
            tools=[
                getFaultDescriptors,
                getDeployedFaults,
                deploySpec,
                deleteFault,
                getFailureAnalysis,
            ],
        )

        self.runner = Runner(
            app_name=APP_NAME,
            agent=self.root_agent,
            artifact_service=self.artifact_service,
            session_service=self.session_service,
            plugins=[TracePlugin()],
        )

        logger.debug(
            "ChaosAgent initialised: model=%s, tools=%s",
            _MODEL,
            [t.__name__ for t in [getFaultDescriptors, getDeployedFaults, deploySpec, deleteFault, getFailureAnalysis]],
        )
