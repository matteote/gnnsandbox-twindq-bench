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
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset
from google.adk.tools.mcp_tool.mcp_session_manager import SseConnectionParams
from agent.prompts import root_prompt
from descriptions import description
import os
import datetime
import logging
from agent_library.agentmiddleware.adk import ADKAgent
from agent_library.trace.trace_plugin import TracePlugin

logger = logging.getLogger(__name__)

class LogsAgent:
    """
    The logs agent.
    """
    SUPPORTED_CONTENT_TYPES = ['text', 'text/plain']

    # static agent instance
    _instance = None

    @classmethod
    async def get_instance(cls):
        if LogsAgent._instance is None:
            LogsAgent._instance = cls()
        return LogsAgent._instance

    def __init__(self):
        self.session_service = InMemorySessionService()
        self.artifact_service = InMemoryArtifactService()

        self.root_agent = LlmAgent(
            name="LogsAgent",
            description=description,
            model="gemini-2.0-flash",
            instruction=root_prompt.format(current_time=datetime.datetime.now().isoformat()),
            tools=[
                MCPToolset(
                    connection_params=SseConnectionParams(
                        url=os.getenv("AGENT_MCP_TOOLS_ADDRESS", "http://127.0.0.1:8080/sse")
                    ),
                    tool_filter=["query_log_entries"]
                )
            ],
        )

        self.app_name = "LogsAgent"
        self.adk_agent = ADKAgent(
            adk_agent=self.root_agent,
            app_name=self.app_name,
            use_in_memory_services=True
        )

        self.runner = Runner(
            app_name="LogsAgent",
            agent=self.root_agent,
            artifact_service=self.artifact_service,
            session_service=self.session_service,
            plugins=[TracePlugin()]
        )   
