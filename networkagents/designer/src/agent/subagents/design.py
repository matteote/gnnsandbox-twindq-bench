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
from agent.subagents.prompts import descriptor_design_prompt
from tools.design import getDesignDoc
from tools.vyos import getVyosDescriptors, getDeployedCRs
import logging

logger = logging.getLogger(__name__)

###################################################
# Descriptor Designer Sub Agent
# -----------------------------
# Given natural language LLD set of steps from 
# previous agent, generate changes needed to
# current deployed vyos descriptors to execute the 
# steps.
###################################################
designer_agent = LlmAgent(
    name="DescriptorDesignerSubAgent",
    model="gemini-3.1-pro-preview",
    instruction=descriptor_design_prompt,
    description="translate network change plan to new vyos descriptors",
    tools=[getDesignDoc, getVyosDescriptors, getDeployedCRs],
    output_key='design'
)