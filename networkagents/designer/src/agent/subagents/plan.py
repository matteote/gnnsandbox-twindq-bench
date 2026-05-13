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
from pydantic import BaseModel, Field
from typing import List, Literal, Optional
import logging
from agent.subagents.plan_prompt import planner_prompt
from tools.design import getDesignDoc
from tools.vyos import getDeployedCRs, getVyosDescriptors

logger = logging.getLogger(__name__)

###################################################
# Network Planner Sub Agent
# -------------------------
# Takes a user change request and produces a
# structured NetworkChangePlan (via output_schema).
#
# The plan is stored in session state under
# 'change_plan' (JSON string) for downstream agents.
#
# If the planner cannot generate a plan with the
# available information it sets needs_clarification
# to a question for the user instead.
#
# The human approval loop is handled entirely in
# agent_executor.py (deterministic, not LLM-driven).
###################################################


class ProposedChange(BaseModel):
    """A single proposed network change."""

    action: Literal['Create', 'Update', 'Delete'] = Field(
        description="The action to take on the CRD. Must be exactly 'Create', 'Update', or 'Delete'."
    )
    resource_type: str = Field(
        description="The resource type: VyOSInfrastructure, VyOSUnderlay, or VyOSL3VPN"
    )
    resource_name: str = Field(
        description="The name of the resource to create, update or delete"
    )
    description: str = Field(
        description=(
            "Technical description including all key parameters needed to generate "
            "the CRD (e.g. RD, RT, interface names, IP addresses, loopback addresses)"
        )
    )
    depends_on: List[str] = Field(
        default=[],
        description="Names of other resources in this plan that must be created first"
    )


class NetworkChangePlan(BaseModel):
    """Structured output from the network planner."""

    needs_clarification: Optional[str] = Field(
        default=None,
        description=(
            "If the planner is missing critical information to produce a correct plan, "
            "set this to a specific question for the user. Leave null if a plan can be generated."
        )
    )
    reasoning: Optional[str] = Field(
        default=None,
        description=(
            "Step-by-step reasoning for the plan based on design rules and current state. "
            "Only populated when proposed_changes is also set."
        )
    )
    proposed_changes: Optional[List[ProposedChange]] = Field(
        default=None,
        description=(
            "The ordered list of proposed network changes. "
            "Only populated when the planner has enough information to generate a complete plan."
        )
    )


planner_agent = LlmAgent(
    name="PlannerSubAgent",
    model="gemini-2.5-flash",
    instruction=planner_prompt,
    description=(
        "Gathers network design information and proposes a structured set of "
        "changes. Returns a clarification question if it needs more information."
    ),
    tools=[getDesignDoc, getVyosDescriptors, getDeployedCRs],
    output_schema=NetworkChangePlan,
    output_key='change_plan',
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)
