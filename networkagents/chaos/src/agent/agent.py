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

from google.adk.agents import LlmAgent
from google.adk import Runner
from google.adk.artifacts import InMemoryArtifactService
from google.adk.sessions import InMemorySessionService
from descriptions import description
from agent.prompts import root_prompt
import logging
from agent_library.trace.trace_plugin import TracePlugin
from tools.faults import getFaultDescriptors, getDeployedFaults, deploySpec, deleteFault

logger = logging.getLogger(__name__)

# Shared app name — both runners operate on the same session namespace
APP_NAME = "ChaosAgent"

class ChaosAgent:
    """
    """

    SUPPORTED_CONTENT_TYPES = ['text', 'text/plain']

    _instance = None

    @classmethod
    async def get_instance(cls):
        if ChaosAgent._instance is None:
            ChaosAgent._instance = cls()
        return ChaosAgent._instance

    def __init__(self):
        self.session_service = InMemorySessionService()
        self.artifact_service = InMemoryArtifactService()

        self.root_agent = LlmAgent(
            name="ChaosAgent",
            description=description,
            model="gemini-2.5-flash",
            instruction=root_prompt,
            tools=[getFaultDescriptors, getDeployedFaults, deploySpec, deleteFault],
        )
        self.runner = Runner(
            app_name=APP_NAME,
            agent=self.root_agent,
            artifact_service=self.artifact_service,
            session_service=self.session_service,
            plugins=[TracePlugin()],
        )
