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
from agent.subagents.prompts import descriptor_approve_prompt
import logging

logger = logging.getLogger(__name__)

###################################################
# Approve L3VPN Descriptors Sub Agent
# ------------------------------------
# Given a set of updated vyos descriptors, evaluate
# if a) the descriptors are syntactically correct
# and b) the impact to the network will be 
# positive. 
###################################################
approval_agent = LlmAgent(
    name="ApproveDescriptorSubAgent",
    model="gemini-2.5-flash",
    instruction=descriptor_approve_prompt,
    description="Approve syntax and design compliance of proposed descriptor changes",
    tools=[],
)