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
from uuid import uuid4
from ag_ui.core import RunAgentInput, UserMessage
import json
import logging

logger = logging.getLogger(__name__)

async def convert_to_run_agent_input(thread_id: str, run_id:str , message: str, initial_state: dict = None):
    """
    Convert incoming data to AG-UI RunAgentInput.
    
    Args:
        data: Either a dict data object or a String text message
        
    Returns:
        RunAgentInput instance
    """
    logger.info(f"Convert message {message} and data {initial_state} with thread {thread_id} and run {run_id} to agent input")

    # Handle simple text message format
    user_message = UserMessage(
        id=str(uuid4()),
        content=message
    )

    state = {}
    if initial_state is not None:
        state=initial_state

    return RunAgentInput(
        thread_id=thread_id,
        run_id=run_id,
        state=state,
        messages=[user_message],
        tools=[],
        context=[],
        forwardedProps={}
    )
