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
import os
from agent.agent_executor import DesignerAgentExecutor
from agent.agent import DesignerAgent
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.server.apps import A2AStarletteApplication
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)
import uvicorn
import descriptions

log_format = "%(asctime)s::%(levelname)s::%(name)s::"\
             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level=logging.INFO, format=log_format)
logger = logging.getLogger(__name__)
BASE_DIR = os.path.dirname(os.path.realpath(__file__))

def get_agent_card(host: str, port: int):
    """Returns the Agent Card for the Designer Agent."""
    capabilities = AgentCapabilities(streaming=True, push_notifications=False)
    skill = AgentSkill(
        id='designer_agent',
        name='Designer Agent',
        description=descriptions.description,
        tags=descriptions.tags,
        examples=descriptions.examples
    )
    return AgentCard(
        name='L3VPN Designer Agent',
        description=descriptions.description,
        url=f'http://{host}:{port}/',
        version='1.0.0',
        default_input_modes=DesignerAgent.SUPPORTED_CONTENT_TYPES,
        default_output_modes=DesignerAgent.SUPPORTED_CONTENT_TYPES,
        capabilities=capabilities,
        skills=[skill],
    )


if __name__ == "__main__":
    logger.info("starting designer agent server...")

    # init the agent class
    request_handler = DefaultRequestHandler(
        agent_executor=DesignerAgentExecutor(),
        task_store=InMemoryTaskStore()
    )

    host = "0.0.0.0"
    port = 8080
    if os.getenv("DEBUG") is not None:
        port = 8088

    server = A2AStarletteApplication(
        agent_card=get_agent_card(host, port), http_handler=request_handler
    )
    uvicorn.run(server.build(), host=host, port=port)
