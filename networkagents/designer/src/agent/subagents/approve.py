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
from google.adk.agents.callback_context import CallbackContext
from google.genai import types
from agent.subagents.approve_prompt import descriptor_approve_prompt
from tools.vyos import getVyosDescriptors
from tools.design import deployDescriptor
import logging

logger = logging.getLogger(__name__)


def deploy_if_approved(callback_context: CallbackContext):
    """
    After the approval agent runs, check whether the descriptors were approved.
    If approved, deploy each YAML document from state['design'] directly.
    Deployment is deterministic Python — no LLM tool-calling required.
    """
    approval_result = callback_context.state.get('approval_result', '')
    if not approval_result or 'APPROVED' not in approval_result.upper():
        logger.info("Descriptors not approved — skipping deployment. Result: %s",
                    str(approval_result)[:120])
        return None

    design_yaml = callback_context.state.get('design', '')
    if not design_yaml:
        logger.error("Approval granted but state['design'] is empty — nothing to deploy")
        return types.Content(
            role='model',
            parts=[types.Part.from_text(
                text="APPROVED but no YAML descriptors found in state to deploy."
            )]
        )

    # Split multi-document YAML on --- separators
    documents = [doc.strip() for doc in design_yaml.split('---') if doc.strip()]
    logger.info("Deploying %d approved descriptor(s)", len(documents))

    results = []
    for doc in documents:
        result = deployDescriptor(doc)
        logger.info("deployDescriptor result: %s", result)
        results.append(result)

    summary = "\n".join(results)
    return types.Content(
        role='model',
        parts=[types.Part.from_text(
            text=f"Deployment complete:\n{summary}"
        )]
    )


###################################################
# Approve L3VPN Descriptors Sub Agent
# ------------------------------------
# Validates that the generated CRD descriptors:
#   a) exactly match the approved plan (one CRD
#      per planned change, correct kind and name)
#   b) are syntactically valid YAML conforming to
#      the VyOS CRD schemas
#
# After running, the after_agent_callback fires:
#   - reads the YAML from state['design']
#   - if APPROVED, calls deployDescriptor() for
#     each document directly in Python
###################################################
approval_agent = LlmAgent(
    name="ApproveDescriptorSubAgent",
    model="gemini-2.5-flash",
    instruction=descriptor_approve_prompt,
    description="Validate that generated descriptors match the approved plan and are schema-compliant",
    tools=[getVyosDescriptors],
    output_key='approval_result',
    after_agent_callback=deploy_if_approved,
)
