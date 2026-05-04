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
from agent.subagents.prompts import descriptor_validate_prompt
from tools.vyos import getDeploymentStatus
import logging

logger = logging.getLogger(__name__)

###################################################
# Validate Descriptors Sub Agent
# ------------------------------
# Watch the descriptor deployment progress and 
# the performance metrics to ensure the changes
# worked and had the desired impact.
###################################################
validate_agent = LlmAgent(
    name="ValidateDescriptorsSubAgent",
    model="gemini-2.5-flash",
    instruction=descriptor_validate_prompt,
    description="Validate descriptor deployment has been successful",
    tools=[getDeploymentStatus],
)