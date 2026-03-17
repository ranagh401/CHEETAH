import json
import re
from typing import Any, Literal, TypedDict

from azure_devops_client import AzureDevOpsClient

try:
    from langchain_openai import AzureChatOpenAI
except ImportError:
    AzureChatOpenAI = None

from langgraph.graph import END, START, StateGraph


FIELD_MAP = {
    "original_estimate": "Microsoft.VSTS.Scheduling.OriginalEstimate",
    "remaining_work": "Microsoft.VSTS.Scheduling.RemainingWork",
    "completed_work": "Microsoft.VSTS.Scheduling.CompletedWork",
    "start_date": "Microsoft.VSTS.Scheduling.StartDate",
    "finish_date": "Microsoft.VSTS.Scheduling.FinishDate",
}
DEFAULT_STATES = ["New", "To Do", "Active", "In Progress", "Resolved", "Closed", "Done"]
DATE_PATTERN = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


class AgentState(TypedDict, total=False):
    user_request: str
    user_email: str
    selected_story_id: int | None
    selected_story: dict[str, Any]
    selected_story_tasks: list[dict[str, Any]]
    conversation_history: list[dict[str, str]]
    plan_reply: str
    action_queue: list[dict[str, Any]]
    result_rows: list[dict[str, Any]]
    refreshed_story_tasks: list[dict[str, Any]]
    touched_items: list[dict[str, Any]]
    execution_log: list[str]
    last_error: str | None
    last_error_source: str | None
    recovery_attempted: bool
    final_response: str
    progress_events: list[str]


class CheetahAgentError(Exception):
    def __init__(self, source: str, detail: str) -> None:
        super().__init__(detail)
        self.source = source
        self.detail = detail


def normalize_work_items(work_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in work_items:
        fields = item.get("fields", {})
        assigned = fields.get("System.AssignedTo")
        if isinstance(assigned, dict):
            assigned_to = assigned.get("displayName") or assigned.get("uniqueName")
        else:
            assigned_to = assigned
        rows.append(
            {
                "ID": fields.get("System.Id"),
                "Type": fields.get("System.WorkItemType"),
                "Title": fields.get("System.Title"),
                "State": fields.get("System.State"),
                "Assigned To": assigned_to,
                "Parent": fields.get("System.Parent"),
                "Remaining Work": fields.get("Microsoft.VSTS.Scheduling.RemainingWork"),
                "Completed Work": fields.get("Microsoft.VSTS.Scheduling.CompletedWork"),
                "Original Estimate": fields.get("Microsoft.VSTS.Scheduling.OriginalEstimate"),
                "Start Date": fields.get("Microsoft.VSTS.Scheduling.StartDate"),
                "Finish Date": fields.get("Microsoft.VSTS.Scheduling.FinishDate"),
                "Changed": fields.get("System.ChangedDate"),
            }
        )
    return rows


def story_context(story: dict[str, Any] | None) -> dict[str, Any]:
    if not story:
        return {}
    fields = story.get("fields", {})
    return {
        "id": fields.get("System.Id"),
        "title": fields.get("System.Title"),
        "state": fields.get("System.State"),
        "description": fields.get("System.Description"),
    }


def recent_task_context(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    context = []
    for item in tasks[:20]:
        fields = item.get("fields", {})
        context.append(
            {
                "id": fields.get("System.Id"),
                "title": fields.get("System.Title"),
                "state": fields.get("System.State"),
                "remaining_work": fields.get("Microsoft.VSTS.Scheduling.RemainingWork"),
                "completed_work": fields.get("Microsoft.VSTS.Scheduling.CompletedWork"),
                "original_estimate": fields.get("Microsoft.VSTS.Scheduling.OriginalEstimate"),
            }
        )
    return context


def map_extra_fields(extra_fields: dict[str, Any] | None) -> dict[str, Any]:
    mapped: dict[str, Any] = {}
    for key, value in (extra_fields or {}).items():
        field_name = FIELD_MAP.get(key)
        if field_name and value not in (None, ""):
            mapped[field_name] = value
    return mapped


def normalize_action(action: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(action)
    nested_fields = normalized.pop("fields", None)
    if isinstance(nested_fields, dict):
        for key, value in nested_fields.items():
            normalized.setdefault(key, value)
    return normalized


def resolve_task_reference(text: str, selected_story_tasks: list[dict[str, Any]]) -> int | None:
    ordered_ids = [item.get("fields", {}).get("System.Id") for item in selected_story_tasks]
    ordered_ids = [item_id for item_id in ordered_ids if item_id is not None]
    ordinal_map = {
        "first": 0,
        "second": 1,
        "third": 2,
        "fourth": 3,
        "fifth": 4,
        "sixth": 5,
        "seventh": 6,
        "eighth": 7,
        "ninth": 8,
        "tenth": 9,
    }
    lower_text = text.lower()
    for word, index in ordinal_map.items():
        if f"{word} task" in lower_text and index < len(ordered_ids):
            return int(ordered_ids[index])
    numbers = [int(match) for match in re.findall(r"\b\d+\b", text)]
    return numbers[0] if numbers else None


def extract_hours(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:hour|hours|hr|hrs)\b", text.lower())
    return float(match.group(1)) if match else None


def extract_date(text: str) -> str | None:
    match = DATE_PATTERN.search(text)
    return match.group(0) if match else None


def extract_state(text: str) -> str | None:
    lower_text = text.lower()
    for candidate in DEFAULT_STATES:
        if candidate.lower() in lower_text:
            return candidate
    if "in progress" in lower_text:
        return "In Progress"
    return None


def fallback_plan(state: AgentState) -> dict[str, Any]:
    text = state["user_request"].strip()
    lower_text = text.lower()
    selected_story_id = state.get("selected_story_id")
    selected_story = state.get("selected_story", {})
    selected_story_tasks = state.get("selected_story_tasks", [])
    user_email = state["user_email"]
    numbers = [int(match) for match in re.findall(r"\b\d+\b", text)]
    actions: list[dict[str, Any]] = []
    reply_parts: list[str] = []

    if any(phrase in lower_text for phrase in ["show tasks", "list tasks", "tasks in this story", "tasks under this story"]):
        actions.append({"action": "list_story_tasks", "parent_id": selected_story_id})
        reply_parts.append("load the tasks under the selected story")

    if "create" in lower_text and "task" in lower_text:
        count_match = re.search(r"create\s+(\d+)", lower_text)
        count = int(count_match.group(1)) if count_match else 1
        parent_id = selected_story_id or (numbers[0] if numbers else None)
        story_title = selected_story.get("title", "Selected story")
        story_description = selected_story.get("description", "")
        task_seed = re.sub(r"\b\d+\b", "", text).strip(" .:-")
        tasks = []
        for index in range(count):
            tasks.append(
                {
                    "title": f"Task {index + 1} - {task_seed[:70] or story_title}",
                    "description": (
                        f"Generated by CHEETAH from request: {text}\n\n"
                        f"User story: {story_title}\n\n"
                        f"Story description context: {story_description}"
                    ),
                }
            )
        actions.append(
            {
                "action": "create_tasks",
                "parent_id": parent_id,
                "assigned_to": user_email,
                "tasks": tasks,
            }
        )
        reply_parts.append(f"create {count} task(s) under the selected story")

    if "comment" in lower_text:
        target_id = resolve_task_reference(text, selected_story_tasks)
        comment_text = text.split(":", 1)[1].strip() if ":" in text else ""
        if target_id and comment_text:
            actions.append({"action": "add_comment", "work_item_id": target_id, "comment": comment_text})
            reply_parts.append(f"add a comment to task {target_id}")

    if any(word in lower_text for word in ["move", "update", "change", "set"]):
        extra_fields: dict[str, Any] = {}
        state_value = extract_state(text)
        hours = extract_hours(text)
        date_value = extract_date(text)

        if "remaining" in lower_text and hours is not None:
            extra_fields["remaining_work"] = hours
        if any(word in lower_text for word in ["completed", "spent", "actual hours"]) and hours is not None:
            extra_fields["completed_work"] = hours
        if "estimate" in lower_text and hours is not None:
            extra_fields["original_estimate"] = hours
        if "start date" in lower_text and date_value:
            extra_fields["start_date"] = date_value
        if any(word in lower_text for word in ["finish date", "end date", "actual date"]) and date_value:
            extra_fields["finish_date"] = date_value

        if any(phrase in lower_text for phrase in ["user story progress", "story progress", "update user story", "update story"]):
            if selected_story_id and state_value:
                actions.append({"action": "update_selected_story", "work_item_id": selected_story_id, "state": state_value})
                reply_parts.append(f"update the selected story {selected_story_id}")

        if "all tasks" in lower_text or "every task" in lower_text:
            actions.append(
                {
                    "action": "bulk_update_story_tasks",
                    "parent_id": selected_story_id,
                    "state": state_value,
                    "extra_fields": extra_fields,
                }
            )
            reply_parts.append("update all tasks under the selected story")
        elif state_value or extra_fields:
            target_id = resolve_task_reference(text, selected_story_tasks)
            if target_id:
                actions.append(
                    {
                        "action": "update_work_item",
                        "work_item_id": target_id,
                        "state": state_value,
                        "extra_fields": extra_fields,
                    }
                )
                reply_parts.append(f"update task {target_id}")

    if actions:
        return {"reply": "I will " + ", then ".join(reply_parts) + ".", "actions": actions}
    return {
        "reply": "I could not map that request safely. Select a story and ask me to create tasks, update tasks, or update the story state.",
        "actions": [],
    }


class CheetahLangGraphAgent:
    def __init__(self, client: AzureDevOpsClient, llm: Any | None) -> None:
        self.client = client
        self.llm = llm
        self.progress_callback: Any | None = None
        self.graph = self._build_graph()

    def _emit_progress(self, text: str) -> None:
        if self.progress_callback is not None:
            self.progress_callback(text)

    def _llm_json(self, prompt: dict[str, Any]) -> dict[str, Any]:
        if self.llm is None:
            raise ValueError("LLM is not configured.")
        try:
            response = self.llm.invoke(
                [
                    (
                        "system",
                        "You are CHEETAH, an Azure DevOps LangGraph agent. Return valid JSON only.",
                    ),
                    ("user", json.dumps(prompt)),
                ]
            )
            content = response.content if isinstance(response.content, str) else json.dumps(response.content)
            content = content.strip()
            if content.startswith("```"):
                content = content.strip("`")
                if content.lower().startswith("json"):
                    content = content[4:].strip()
            return json.loads(content)
        except Exception as exc:
            raise CheetahAgentError("Azure OpenAI", f"{type(exc).__name__}: {exc}") from exc

    def _plan_request(self, state: AgentState) -> dict[str, Any]:
        self._emit_progress("Understanding the request")
        if self.llm is None:
            plan = fallback_plan(state)
            return {"plan_reply": plan["reply"], "action_queue": plan["actions"]}

        prompt = {
            "conversation_history": state.get("conversation_history", [])[-8:],
            "selected_story": state.get("selected_story", {}),
            "selected_story_tasks": recent_task_context(state.get("selected_story_tasks", [])),
            "user_email": state["user_email"],
            "user_request": state["user_request"],
            "supported_actions": [
                "list_story_tasks",
                "create_tasks",
                "update_selected_story",
                "update_work_item",
                "bulk_update_story_tasks",
                "add_comment",
            ],
            "rules": [
                "Return JSON only.",
                "If the user asks for multiple things, return multiple actions in execution order.",
                "Use the selected story title and description to generate task titles and descriptions.",
                "Use update_selected_story only for story-level changes like state/title.",
                "Do not put task-only scheduling fields on update_selected_story.",
                "For ordinal references like third task, resolve against the provided selected_story_tasks order.",
                "Put action parameters directly on each action object.",
            ],
            "output_shape": {"reply": "short message", "actions": [{"action": "name"}]},
        }
        plan = self._llm_json(prompt)
        actions = [normalize_action(action) for action in plan.get("actions", [])]
        self._emit_progress(f"Planned {len(actions)} action(s)")
        return {"plan_reply": plan.get("reply", "I will handle that."), "action_queue": actions}

    def _execute_actions(self, state: AgentState) -> dict[str, Any]:
        self._emit_progress("Executing actions")
        result_rows: list[dict[str, Any]] = []
        refreshed_story_tasks: list[dict[str, Any]] = []
        touched_items: list[dict[str, Any]] = []
        execution_log: list[str] = []

        try:
            for raw_action in state.get("action_queue", []):
                action = normalize_action(raw_action)
                action_name = action.get("action")
                selected_story_id = state.get("selected_story_id")
                self._emit_progress(f"Running {action_name}")

                if action_name == "list_story_tasks":
                    story_id = action.get("parent_id") or selected_story_id
                    ids = self.client.query_child_tasks(story_id)
                    refreshed_story_tasks = self.client.get_work_items(ids)
                    result_rows.extend(normalize_work_items(refreshed_story_tasks))
                    touched_items = refreshed_story_tasks
                    execution_log.append(f"Loaded {len(refreshed_story_tasks)} task(s) under story {story_id}.")
                    self._emit_progress(f"Loaded tasks for story {story_id}")
                    continue

                if action_name == "create_tasks":
                    parent_id = action.get("parent_id") or selected_story_id
                    assigned_to = action.get("assigned_to") or state["user_email"]
                    created = []
                    for task in action.get("tasks", []):
                        created_item = self.client.create_task(
                            title=task.get("title", "New task"),
                            description=task.get("description", ""),
                            assigned_to=assigned_to,
                            parent_id=parent_id,
                        )
                        created.append(created_item)
                    refreshed_story_tasks = self.client.get_work_items(self.client.query_child_tasks(parent_id))
                    touched_items = created
                    result_rows.extend(normalize_work_items(created))
                    execution_log.append(f"Created {len(created)} task(s) under story {parent_id}.")
                    self._emit_progress(f"Created {len(created)} task(s)")
                    continue

                if action_name == "update_selected_story":
                    work_item_id = int(action.get("work_item_id") or selected_story_id)
                    self.client.update_work_item(
                        work_item_id,
                        state=action.get("state"),
                        title=action.get("title"),
                    )
                    updated = self.client.get_work_items([work_item_id])
                    touched_items.extend(updated)
                    result_rows.extend(normalize_work_items(updated))
                    execution_log.append(f"Updated selected story {work_item_id}.")
                    self._emit_progress(f"Updated story {work_item_id}")
                    continue

                if action_name == "update_work_item":
                    work_item_id = int(action["work_item_id"])
                    self.client.update_work_item(
                        work_item_id,
                        state=action.get("state"),
                        assigned_to=action.get("assigned_to"),
                        title=action.get("title"),
                        extra_fields=map_extra_fields(action.get("extra_fields")),
                    )
                    updated = self.client.get_work_items([work_item_id])
                    touched_items.extend(updated)
                    result_rows.extend(normalize_work_items(updated))
                    execution_log.append(f"Updated work item {work_item_id}.")
                    self._emit_progress(f"Updated work item {work_item_id}")
                    continue

                if action_name == "bulk_update_story_tasks":
                    parent_id = action.get("parent_id") or selected_story_id
                    task_ids = self.client.query_child_tasks(parent_id)
                    for task in self.client.get_work_items(task_ids):
                        task_id = task.get("fields", {}).get("System.Id")
                        if task_id is None:
                            continue
                        self.client.update_work_item(
                            int(task_id),
                            state=action.get("state"),
                            extra_fields=map_extra_fields(action.get("extra_fields")),
                        )
                    refreshed_story_tasks = self.client.get_work_items(self.client.query_child_tasks(parent_id))
                    touched_items = refreshed_story_tasks
                    result_rows.extend(normalize_work_items(refreshed_story_tasks))
                    execution_log.append(f"Updated {len(refreshed_story_tasks)} task(s) under story {parent_id}.")
                    self._emit_progress(f"Updated {len(refreshed_story_tasks)} task(s)")
                    continue

                if action_name == "add_comment":
                    work_item_id = int(action["work_item_id"])
                    self.client.add_comment(work_item_id, action.get("comment", ""))
                    updated = self.client.get_work_items([work_item_id])
                    touched_items.extend(updated)
                    result_rows.extend(normalize_work_items(updated))
                    execution_log.append(f"Added comment to work item {work_item_id}.")
                    self._emit_progress(f"Added comment to {work_item_id}")
                    continue

            return {
                "result_rows": result_rows,
                "refreshed_story_tasks": refreshed_story_tasks,
                "touched_items": touched_items,
                "execution_log": execution_log,
                "last_error": None,
                "last_error_source": None,
            }
        except Exception as exc:
            self._emit_progress("Execution failed, checking recovery path")
            return {
                "last_error": f"{type(exc).__name__}: {exc}",
                "last_error_source": "Azure DevOps",
                "execution_log": execution_log,
            }

    def _route_after_execute(self, state: AgentState) -> Literal["recover", "finalize"]:
        if state.get("last_error") and not state.get("recovery_attempted"):
            return "recover"
        return "finalize"

    def _recover(self, state: AgentState) -> dict[str, Any]:
        self._emit_progress("Inspecting the error and preparing a retry")
        error_text = state.get("last_error") or ""
        original_actions = state.get("action_queue", [])

        if self.llm is None:
            repaired = []
            for action in original_actions:
                normalized = normalize_action(action)
                if normalized.get("action") == "update_selected_story" and normalized.get("state") == "In Progress":
                    normalized["state"] = "Active"
                repaired.append(normalized)
            self._emit_progress("Retrying with a repaired plan")
            return {
                "action_queue": repaired,
                "recovery_attempted": True,
                "last_error": None,
                "last_error_source": None,
            }

        prompt = {
            "selected_story": state.get("selected_story", {}),
            "selected_story_tasks": recent_task_context(state.get("selected_story_tasks", [])),
            "user_request": state["user_request"],
            "failed_actions": original_actions,
            "azure_devops_error": error_text,
            "instruction": (
                "Repair the action list so it will succeed. "
                "Common fixes: map invalid story states like 'In Progress' to 'Active', "
                "remove invalid story-level scheduling fields, preserve user intent."
            ),
            "output_shape": {"reply": "short repair note", "actions": [{"action": "name"}]},
        }
        repaired = self._llm_json(prompt)
        self._emit_progress("Retrying with a repaired plan")
        return {
            "action_queue": [normalize_action(action) for action in repaired.get("actions", [])],
            "plan_reply": repaired.get("reply", state.get("plan_reply", "")),
            "recovery_attempted": True,
            "last_error": None,
            "last_error_source": None,
        }

    def _finalize(self, state: AgentState) -> dict[str, Any]:
        self._emit_progress("Preparing the final response")
        if state.get("last_error"):
            source = state.get("last_error_source") or "Unknown source"
            return {
                "final_response": (
                    f"MAA KAA BHOSRAAA AHHHHH\n\n{source} error:\n"
                    f"```text\n{state['last_error']}\n```"
                )
            }

        execution_log = state.get("execution_log", [])
        if execution_log:
            response = f"{state.get('plan_reply', 'Done.')}\n\n" + "\n".join(f"- {line}" for line in execution_log)
        else:
            response = state.get("plan_reply", "Done.")
        return {"final_response": response}

    def _build_graph(self):
        graph = StateGraph(AgentState)
        graph.add_node("plan", self._plan_request)
        graph.add_node("execute", self._execute_actions)
        graph.add_node("recover", self._recover)
        graph.add_node("finalize", self._finalize)
        graph.add_edge(START, "plan")
        graph.add_edge("plan", "execute")
        graph.add_conditional_edges("execute", self._route_after_execute, {"recover": "recover", "finalize": "finalize"})
        graph.add_edge("recover", "execute")
        graph.add_edge("finalize", END)
        return graph.compile()

    def invoke_turn(
        self,
        *,
        user_request: str,
        user_email: str,
        selected_story_id: int | None,
        selected_story: dict[str, Any] | None,
        selected_story_tasks: list[dict[str, Any]],
        conversation_history: list[dict[str, str]],
        progress_callback: Any | None = None,
    ) -> dict[str, Any]:
        self.progress_callback = progress_callback
        result = self.graph.invoke(
            {
                "user_request": user_request,
                "user_email": user_email,
                "selected_story_id": selected_story_id,
                "selected_story": story_context(selected_story),
                "selected_story_tasks": selected_story_tasks,
                "conversation_history": conversation_history,
                "recovery_attempted": False,
            }
        )
        self.progress_callback = None
        return {
            "assistant_message": result.get("final_response", "Done."),
            "rows": result.get("result_rows", []),
            "refreshed_story_tasks": result.get("refreshed_story_tasks", []),
            "touched_items": result.get("touched_items", []),
        }


def build_langgraph_llm(
    *,
    endpoint: str,
    api_key: str,
    api_version: str,
    deployment: str,
) -> Any | None:
    if AzureChatOpenAI is None or not all([endpoint, api_key, api_version, deployment]):
        return None
    return AzureChatOpenAI(
        azure_endpoint=endpoint,
        api_key=api_key,
        api_version=api_version,
        azure_deployment=deployment,
        temperature=0.1,
    )
