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

import json
import logging
from typing import Optional, Tuple
from typing_extensions import override
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import (
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from google.genai import types
from agent.agent import DesignerAgent
from agent.subagents.plan import NetworkChangePlan
from a2a.utils import new_task, new_agent_text_message
from utils.error_handler import (
    DesignerAgentError,
    ErrorSeverity,
    create_error_status_event,
)
from agent_library.trace.trace_context import TracingContext

logger = logging.getLogger(__name__)

# ── State-machine values ───────────────────────────────────────────────────────
_STATE_ASKING = 'asking_clarification'   # planner asked the user a question
_STATE_APPROVAL = 'awaiting_approval'    # plan built, waiting for human decision


class DesignerAgentExecutor(AgentExecutor):
    """
    Designer AgentExecutor.

    Implements a human-in-the-loop planning loop using ADK runners and A2A task
    state.  The state machine is keyed by task.id (NOT context.context_id).

    Why task.id and not context.context_id?
      context.context_id is taken directly from the incoming message's contextId
      field, which the supervisor may or may not set.  If contextId is absent on
      turn 1 it will be None, so the state is stored under None; on turn 2 the
      supervisor supplies a real session UUID, the lookup returns None, and the
      planner re-runs with the approval JSON as if it were a new request.

      task.id is always a UUID: it is generated on turn 1 (when we call
      new_task()) and retrieved from the A2A task store on every subsequent turn
      via the taskId the supervisor echoes back.  This guarantees a consistent
      key across all HTTP turns regardless of whether contextId is present.

    State machine (stored in agent._states[task.id]):

      None / not set  →  run planner_runner with the user's message
                         ├─ plan generated        → STATE_APPROVAL, emit input_required [PLAN]
                         └─ needs clarification   → STATE_ASKING, emit input_required [QUESTION]

      STATE_ASKING    →  re-run planner_runner with the user's answer
                         (full session history gives the planner context)

      STATE_APPROVAL  →  parse user response (JSON from widget or plain text):
                         ├─ approved              → clear state, run execution_runner
                         └─ rejected + feedback   → clear plan, re-run planner with feedback
    """

    @override
    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """Main A2A message handler."""
        logger.info("DesignerAgentExecutor.execute called")
        query = context.get_user_input()
        task = context.current_task

        TracingContext.set_trace_id(context.context_id)

        if not context.message:
            raise DesignerAgentError(
                message='No message provided',
                severity=ErrorSeverity.ERROR
            )

        if not task:
            logger.info("Creating new task")
            task = new_task(context.message)
            await event_queue.enqueue_event(task)

        # Use task.id as the stable state-machine / ADK-session key.
        # context.context_id can be None if the supervisor omits contextId in
        # the message, while task.id is always a UUID.
        session_key = task.id
        logger.info("Processing: %r  task_id=%s  context_id=%s",
                    query[:80], session_key, context.context_id)

        try:
            agent = await DesignerAgent.get_instance()

            session = await agent.session_service.get_session(
                app_name="DesignerSupervisorAgent",
                user_id="agent",
                session_id=session_key,
            )
            if session is None:
                logger.info("Creating new ADK session for task %s", session_key)
                session = await agent.session_service.create_session(
                    app_name="DesignerSupervisorAgent",
                    user_id="agent",
                    session_id=session_key,
                    # Pre-populate change_plan so ADK's prompt substitution never
                    # raises a KeyError before the planner has run.
                    state={'change_plan': ''},
                )

            current_state = agent._states.get(session_key)
            logger.info("Current designer state: %s  (key=%s)", current_state, session_key)

            if current_state in (_STATE_ASKING, _STATE_APPROVAL):
                # Turn 2+: handle the human's response
                await self._handle_response(
                    agent=agent,
                    session=session,
                    user_input=query,
                    task=task,
                    session_key=session_key,
                    event_queue=event_queue,
                )
            else:
                # Turn 1: first run — record the original request so we can
                # include it in any subsequent re-planning messages.
                agent._original_requests[session_key] = query
                content = types.Content(
                    role='user',
                    parts=[types.Part.from_text(text=query)],
                )
                await self._run_planner(
                    agent=agent,
                    session=session,
                    message_content=content,
                    task=task,
                    session_key=session_key,
                    event_queue=event_queue,
                )

        except DesignerAgentError as e:
            if task:
                await event_queue.enqueue_event(
                    create_error_status_event(
                        error=e,
                        context_id=task.context_id,
                        task_id=task.id,
                        final=True,
                    )
                )
            raise
        except Exception as e:
            error = DesignerAgentError(
                message=f"Unexpected error in execute: {str(e)}",
                severity=ErrorSeverity.ERROR,
                original_exception=e,
            )
            if task:
                await event_queue.enqueue_event(
                    create_error_status_event(
                        error=error,
                        context_id=task.context_id,
                        task_id=task.id,
                        final=True,
                    )
                )
            logger.error("Unexpected error in execute: %s", str(e), exc_info=True)
            raise error

    # ── Internal helpers ───────────────────────────────────────────────────────

    async def _run_planner(
        self,
        agent: DesignerAgent,
        session,
        message_content: types.Content,
        task,
        session_key: str,
        event_queue: EventQueue,
    ) -> None:
        """
        Run the planner_runner to completion.

        After the run, inspects session.state['change_plan'] to determine
        whether the planner:
          (a) produced a plan  → STATE_APPROVAL, emit input_required [PLAN]
          (b) needs more info  → STATE_ASKING,   emit input_required [QUESTION]
        """
        logger.info("Running planner_runner  session_key=%s", session_key)

        async for event in agent.planner_runner.run_async(
            user_id="agent",
            session_id=session_key,
            new_message=message_content,
        ):
            logger.info("PLANNER EVENT: %s", event)

        # Re-fetch session to get the updated state written by output_key
        session = await agent.session_service.get_session(
            app_name="DesignerSupervisorAgent",
            user_id="agent",
            session_id=session_key,
        )

        plan_raw = session.state.get('change_plan') if session else None
        if not plan_raw:
            logger.error("Planner produced no output (change_plan is empty)")
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    status=TaskStatus(
                        state=TaskState.failed,
                        message=new_agent_text_message(
                            "The planner produced no output. Please try again.",
                            task.context_id, task.id,
                        ),
                    ),
                    final=True,
                    context_id=task.context_id,
                    task_id=task.id,
                )
            )
            return

        try:
            # ADK stores output_schema results as a dict in session state (not a
            # JSON string).  Validate and normalise so downstream code always
            # gets a proper Python model object.
            if isinstance(plan_raw, dict):
                plan = NetworkChangePlan.model_validate(plan_raw)
            else:
                plan = NetworkChangePlan.model_validate_json(plan_raw)
        except Exception as e:
            logger.error("Failed to parse planner output as NetworkChangePlan: %s", e)
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    status=TaskStatus(
                        state=TaskState.failed,
                        message=new_agent_text_message(
                            f"Planner produced invalid output: {e}",
                            task.context_id, task.id,
                        ),
                    ),
                    final=True,
                    context_id=task.context_id,
                    task_id=task.id,
                )
            )
            return

        if plan.needs_clarification:
            logger.info("Planner needs clarification: %s", plan.needs_clarification)
            agent._states[session_key] = _STATE_ASKING
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    status=TaskStatus(
                        state=TaskState.input_required,
                        message=new_agent_text_message(
                            f"[QUESTION] {plan.needs_clarification}",
                            task.context_id, task.id,
                        ),
                    ),
                    final=True,
                    context_id=task.context_id,
                    task_id=task.id,
                )
            )

        elif plan.proposed_changes is not None:
            logger.info(
                "Planner produced a plan with %d changes", len(plan.proposed_changes)
            )
            agent._states[session_key] = _STATE_APPROVAL
            approval_text = self._format_plan_for_approval(plan)
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    status=TaskStatus(
                        state=TaskState.input_required,
                        message=new_agent_text_message(
                            approval_text, task.context_id, task.id,
                        ),
                    ),
                    final=True,
                    context_id=task.context_id,
                    task_id=task.id,
                )
            )

        else:
            logger.warning("Planner produced null needs_clarification and null proposed_changes")
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    status=TaskStatus(
                        state=TaskState.completed,
                        message=new_agent_text_message(
                            "The planner could not generate a plan. "
                            "Please refine your request.",
                            task.context_id, task.id,
                        ),
                    ),
                    final=True,
                    context_id=task.context_id,
                    task_id=task.id,
                )
            )

    async def _handle_response(
        self,
        agent: DesignerAgent,
        session,
        user_input: str,
        task,
        session_key: str,
        event_queue: EventQueue,
    ) -> None:
        """
        Handle the human's reply to either a clarification question or a plan
        approval request.

        - STATE_ASKING:   re-run the planner with the user's answer
        - STATE_APPROVAL: approved → run execution; rejected → re-run planner
        """
        current_state = agent._states.get(session_key)
        logger.info("_handle_response: current_state=%s  session_key=%s",
                    current_state, session_key)

        if current_state == _STATE_ASKING:
            logger.info("Handling clarification answer: %r", user_input[:80])
            content = types.Content(
                role='user',
                parts=[types.Part.from_text(text=user_input)],
            )
            agent._states.pop(session_key, None)
            await self._run_planner(
                agent=agent,
                session=session,
                message_content=content,
                task=task,
                session_key=session_key,
                event_queue=event_queue,
            )

        elif current_state == _STATE_APPROVAL:
            approved, feedback = self._parse_approval_response(user_input)
            logger.info(
                "Approval response: approved=%s feedback=%r",
                approved, feedback[:80] if feedback else ''
            )

            if approved:
                agent._states.pop(session_key, None)
                await self._run_execution(
                    agent=agent,
                    task=task,
                    session_key=session_key,
                    event_queue=event_queue,
                )
            else:
                # User rejected — re-plan with the original request + feedback so
                # the planner generates a COMPLETE revised plan, not just a delta.
                agent._states.pop(session_key, None)
                original = agent._original_requests.get(session_key, '')
                if feedback and original:
                    feedback_msg = (
                        f"Original network change request:\n{original}\n\n"
                        f"The previous plan was rejected. Reviewer feedback:\n{feedback}\n\n"
                        f"Please generate a COMPLETE revised plan for the original request "
                        f"above, incorporating the reviewer's feedback. Your proposed_changes "
                        f"must contain ALL changes needed to fulfil the original request — "
                        f"not just the amended items."
                    )
                elif feedback:
                    feedback_msg = (
                        f"The previous plan was rejected. Reviewer feedback:\n{feedback}\n\n"
                        f"Please generate a COMPLETE revised plan incorporating this feedback."
                    )
                else:
                    feedback_msg = (
                        "The plan was rejected. Please generate a COMPLETE revised plan "
                        "for the original request."
                    )
                content = types.Content(
                    role='user',
                    parts=[types.Part.from_text(text=feedback_msg)],
                )
                await self._run_planner(
                    agent=agent,
                    session=session,
                    message_content=content,
                    task=task,
                    session_key=session_key,
                    event_queue=event_queue,
                )
        else:
            logger.warning("Unexpected state %r — re-running planner", current_state)
            content = types.Content(
                role='user',
                parts=[types.Part.from_text(text=user_input)],
            )
            await self._run_planner(
                agent=agent,
                session=session,
                message_content=content,
                task=task,
                session_key=session_key,
                event_queue=event_queue,
            )

    async def _run_execution(
        self,
        agent: DesignerAgent,
        task,
        session_key: str,
        event_queue: EventQueue,
    ) -> None:
        """
        Run the execution pipeline (descriptor designer → approver → deployer → validator).
        Streams working events to the socket and emits a final completed event.

        NOTE: A SequentialAgent produces mostly tool-call events with no text
        content.  We therefore track whether a final=True event was emitted and
        always emit a guaranteed completed event at the end so the supervisor's
        send_streaming_task terminates and the approval widget is dismissed.
        """
        logger.info("Running execution pipeline  session_key=%s", session_key)

        # Immediately emit a working event so the UI transitions away from
        # input_required (approval widget dismisses) and shows progress.
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                status=TaskStatus(
                    state=TaskState.working,
                    message=new_agent_text_message(
                        "Executing approved network changes…",
                        task.context_id, task.id,
                    ),
                ),
                final=False,
                context_id=task.context_id,
                task_id=task.id,
            )
        )

        # Read the plan from the ADK session and serialise to proper JSON.
        # We do NOT rely on {change_plan} prompt substitution because ADK stores
        # output_key values as Python dicts; str(dict) yields Python repr
        # (single quotes etc.) which is not valid JSON for the descriptor agent.
        session = await agent.session_service.get_session(
            app_name="DesignerSupervisorAgent",
            user_id="agent",
            session_id=session_key,
        )
        plan_raw = session.state.get('change_plan', {}) if session else {}
        if isinstance(plan_raw, str):
            plan_json = plan_raw if plan_raw else '{}'
        else:
            plan_json = json.dumps(plan_raw, indent=2)

        content = types.Content(
            role='user',
            parts=[types.Part.from_text(
                text=(
                    "Execute the following approved network change plan.\n\n"
                    "approved_plan:\n"
                    f"```json\n{plan_json}\n```"
                )
            )],
        )

        emitted_final = False
        last_text = "Network changes completed successfully."

        async for event in agent.execution_runner.run_async(
            user_id="agent",
            session_id=session_key,
            new_message=content,
        ):
            logger.info("EXECUTION EVENT: %s", event)
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        logger.info("** %s: %s", event.author, part.text[:120])
                        last_text = part.text
                        is_final = event.is_final_response()
                        await event_queue.enqueue_event(
                            TaskStatusUpdateEvent(
                                status=TaskStatus(
                                    state=TaskState.completed if is_final else TaskState.working,
                                    message=new_agent_text_message(
                                        part.text, task.context_id, task.id
                                    ),
                                ),
                                final=is_final,
                                context_id=task.context_id,
                                task_id=task.id,
                            )
                        )
                        if is_final:
                            emitted_final = True

        # Guarantee a completed event so the supervisor's streaming call always
        # terminates, even if the SequentialAgent produced no text-bearing events.
        if not emitted_final:
            logger.info("Execution loop ended without a final event — emitting fallback completed")
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    status=TaskStatus(
                        state=TaskState.completed,
                        message=new_agent_text_message(
                            last_text, task.context_id, task.id,
                        ),
                    ),
                    final=True,
                    context_id=task.context_id,
                    task_id=task.id,
                )
            )

    # ── Formatting helpers ─────────────────────────────────────────────────────

    def _format_plan_for_approval(self, plan: NetworkChangePlan) -> str:
        """
        Format the proposed_changes list as a bullet list for the approval widget.
        Reasoning is intentionally omitted — only the actions are shown to the user.
        """
        lines = []
        for change in (plan.proposed_changes or []):
            lines.append(
                f"* **{change.action}** `{change.resource_type}` — "
                f"**{change.resource_name}**: {change.description}"
            )
            if change.depends_on:
                lines.append(f"  *Depends on*: {', '.join(change.depends_on)}")
        return "[PLAN]\n" + "\n".join(lines)

    def _parse_approval_response(self, user_input: str) -> Tuple[bool, str]:
        """
        Parse the human's approval response.

        Handles:
        - JSON from requestTaskApproval widget: {"approved": true/false, "feedback": "..."}
        - Simple yes/no text
        - Any other text → treated as rejected + feedback (triggers re-planning)

        Returns (approved: bool, feedback: str)
        """
        # Try JSON (from the approval widget via the supervisor)
        try:
            data = json.loads(user_input)
            if isinstance(data, dict) and 'approved' in data:
                approved = bool(data['approved'])
                feedback = str(data.get('feedback', '') or '')
                logger.info("Parsed JSON approval: approved=%s", approved)
                return approved, feedback
        except (json.JSONDecodeError, ValueError):
            pass

        # Plain text
        cleaned = user_input.strip().lower()
        if cleaned in ('yes', 'y', 'approve', 'approved', 'ok', 'proceed', 'confirm'):
            return True, ''
        if cleaned in ('no', 'n', 'deny', 'denied', 'cancel', 'cancelled', 'reject', 'rejected'):
            return False, ''

        # Unrecognised text → treat as rejection with textual feedback (re-plan with it)
        logger.info("Treating unrecognised response as rejection with feedback: %r", user_input[:80])
        return False, user_input

    # ── Cancel ─────────────────────────────────────────────────────────────────

    @override
    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        """Handler for cancel requests."""
        task = context.current_task

        try:
            if not task:
                raise DesignerAgentError(
                    message='Cannot cancel: No active task found',
                    severity=ErrorSeverity.WARNING,
                )

            logger.warning("Cancel requested for task %s", task.id)
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    status=TaskStatus(
                        state=TaskState.cancelled,
                        message=new_agent_text_message(
                            "Task cancellation requested. "
                            "Some operations may continue in the background.",
                            task.context_id, task.id,
                        ),
                    ),
                    final=True,
                    context_id=task.context_id,
                    task_id=task.id,
                )
            )
        except DesignerAgentError as e:
            if task:
                await event_queue.enqueue_event(
                    create_error_status_event(
                        error=e,
                        context_id=task.context_id,
                        task_id=task.id,
                        final=True,
                    )
                )
            raise
        except Exception as e:
            error = DesignerAgentError(
                message=f"Unexpected error in cancel: {str(e)}",
                severity=ErrorSeverity.ERROR,
                original_exception=e,
            )
            if task:
                await event_queue.enqueue_event(
                    create_error_status_event(
                        error=error,
                        context_id=task.context_id,
                        task_id=task.id,
                        final=True,
                    )
                )
            logger.error("Unexpected error in cancel: %s", str(e), exc_info=True)
            raise error
