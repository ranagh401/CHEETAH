from cheetah_app_langgraph import main

main()
raise SystemExit
r"""

import json
import os
import re
from typing import Any

import streamlit as st
from dotenv import load_dotenv

from azure_devops_client import AzureDevOpsClient

try:
    from openai import AzureOpenAI
except ImportError:
    AzureOpenAI = None


load_dotenv()

DEFAULT_USER_EMAIL = os.getenv("AZDO_USER_EMAIL", "").strip()
PRIMARY_ALLOWED_EMAIL = "pratham.rana@polestaranalytics.com"
ACCESS_PHRASE = "ranagoat"
DEFAULT_WORK_ITEM_TYPES = ["Any", "User Story", "Task", "Bug", "Feature"]
DEFAULT_STATES = ["Any", "New", "To Do", "Active", "In Progress", "Resolved", "Closed", "Done"]
DATE_PATTERN = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
FIELD_MAP = {
    "original_estimate": "Microsoft.VSTS.Scheduling.OriginalEstimate",
    "remaining_work": "Microsoft.VSTS.Scheduling.RemainingWork",
    "completed_work": "Microsoft.VSTS.Scheduling.CompletedWork",
    "start_date": "Microsoft.VSTS.Scheduling.StartDate",
    "finish_date": "Microsoft.VSTS.Scheduling.FinishDate",
}


def get_env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def build_azure_devops_client() -> AzureDevOpsClient | None:
    org_url = get_env("AZDO_ORG_URL")
    project = get_env("AZDO_PROJECT")
    pat = get_env("AZDO_PAT")
    if not all([org_url, project, pat]):
        return None
    return AzureDevOpsClient(org_url=org_url, project=project, pat=pat)


def build_openai_client() -> AzureOpenAI | None:
    endpoint = get_env("AZURE_OPENAI_ENDPOINT")
    api_key = get_env("AZURE_OPENAI_API_KEY")
    api_version = get_env("AZURE_OPENAI_API_VERSION")
    if not all([endpoint, api_key, api_version]) or AzureOpenAI is None:
        return None
    return AzureOpenAI(
        azure_endpoint=endpoint,
        api_key=api_key,
        api_version=api_version,
    )


def load_assigned_user_stories(client: AzureDevOpsClient, user_email: str) -> list[dict[str, Any]]:
    ids = client.query_assigned_user_stories(user_email=user_email)
    return client.get_work_items(ids)


def load_story_tasks(client: AzureDevOpsClient, story_id: int | None) -> list[dict[str, Any]]:
    if not story_id:
        return []
    ids = client.query_child_tasks(parent_id=story_id)
    return client.get_work_items(ids)


def get_story_item_from_options(story_id: int | None) -> dict[str, Any] | None:
    if not story_id:
        return None
    for item in st.session_state.get("story_options", []):
        if item.get("fields", {}).get("System.Id") == story_id:
            return item
    return None


def is_access_allowed(user_email: str) -> bool:
    normalized_email = user_email.strip().lower()
    if normalized_email == PRIMARY_ALLOWED_EMAIL:
        st.session_state["access_granted"] = True
        st.session_state["access_email"] = normalized_email
        return True

    if (
        st.session_state.get("access_granted")
        and st.session_state.get("access_email") == normalized_email
    ):
        return True

    return False


def init_session_state() -> None:
    st.session_state.setdefault(
        "messages",
        [
            {
                "role": "assistant",
                "content": (
                    "I am CHEETAH. Select one of your user stories, then tell me what to do there.\n"
                    "- Show tasks in this story\n"
                    "- Create 5 testing tasks under this story\n"
                    "- Move all tasks in this story to Active\n"
                    "- Set remaining work to 3 hours on task 67890\n"
                    "- Set actual date to 2026-03-20 for every task in this story"
                ),
            }
        ],
    )
    st.session_state.setdefault("story_options", [])
    st.session_state.setdefault("selected_story_id", None)
    st.session_state.setdefault("selected_story_label", "")
    st.session_state.setdefault("selected_story_tasks", [])
    st.session_state.setdefault("work_items", [])
    st.session_state.setdefault("active_work_item_id", None)
    st.session_state.setdefault("access_granted", False)
    st.session_state.setdefault("access_email", "")


def format_assigned_to(value: Any) -> str:
    if isinstance(value, dict):
        return value.get("displayName") or value.get("uniqueName") or ""
    return str(value or "")


def normalize_work_items(work_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in work_items:
        fields = item.get("fields", {})
        rows.append(
            {
                "ID": fields.get("System.Id"),
                "Type": fields.get("System.WorkItemType"),
                "Title": fields.get("System.Title"),
                "State": fields.get("System.State"),
                "Assigned To": format_assigned_to(fields.get("System.AssignedTo")),
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


def recent_context(work_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    context = []
    for item in work_items[:12]:
        fields = item.get("fields", {})
        context.append(
            {
                "id": fields.get("System.Id"),
                "title": fields.get("System.Title"),
                "type": fields.get("System.WorkItemType"),
                "state": fields.get("System.State"),
                "parent": fields.get("System.Parent"),
                "remaining_work": fields.get("Microsoft.VSTS.Scheduling.RemainingWork"),
                "completed_work": fields.get("Microsoft.VSTS.Scheduling.CompletedWork"),
                "start_date": fields.get("Microsoft.VSTS.Scheduling.StartDate"),
                "finish_date": fields.get("Microsoft.VSTS.Scheduling.FinishDate"),
            }
        )
    return context


def story_context(item: dict[str, Any] | None) -> dict[str, Any]:
    if not item:
        return {}
    fields = item.get("fields", {})
    return {
        "id": fields.get("System.Id"),
        "title": fields.get("System.Title"),
        "state": fields.get("System.State"),
        "description": fields.get("System.Description"),
    }


def story_label(item: dict[str, Any]) -> str:
    fields = item.get("fields", {})
    return f"{fields.get('System.Id')} | {fields.get('System.Title')} | {fields.get('System.State')}"


def extract_hours(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:hour|hours|hr|hrs)\b", text.lower())
    if match:
        return float(match.group(1))
    return None


def extract_date(text: str) -> str | None:
    match = DATE_PATTERN.search(text)
    return match.group(0) if match else None


def extract_state(text: str) -> str | None:
    lower_text = text.lower()
    for candidate in DEFAULT_STATES[1:]:
        if candidate.lower() in lower_text:
            return candidate
    return None


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


def fallback_plan(
    user_message: str,
    selected_story_id: int | None,
    selected_story_item: dict[str, Any] | None,
    selected_story_tasks: list[dict[str, Any]],
    user_email: str,
) -> dict[str, Any]:
    text = user_message.strip()
    lower_text = text.lower()
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
        task_seed = re.sub(r"\b\d+\b", "", text).strip(" .:-")
        story_title = (selected_story_item or {}).get("fields", {}).get("System.Title", "Selected story")
        story_description = (selected_story_item or {}).get("fields", {}).get("System.Description", "")
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
        target_id = numbers[0] if numbers else None
        comment_text = text.split(":", 1)[1].strip() if ":" in text else ""
        if target_id:
            actions.append(
                {
                    "action": "add_comment",
                    "work_item_id": target_id,
                    "comment": comment_text,
                }
            )
            reply_parts.append(f"add a comment to task {target_id}")

    if any(word in lower_text for word in ["move", "update", "change", "set"]):
        extra_fields: dict[str, Any] = {}
        state = extract_state(text)
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
            if selected_story_id and state:
                actions.append(
                    {
                        "action": "update_selected_story",
                        "work_item_id": selected_story_id,
                        "state": state,
                    }
                )
                reply_parts.append(f"update the selected story {selected_story_id}")

        if "all tasks" in lower_text or "every task" in lower_text:
            actions.append(
                {
                    "action": "bulk_update_story_tasks",
                    "parent_id": selected_story_id,
                    "state": state,
                    "extra_fields": extra_fields,
                }
            )
            reply_parts.append("update all tasks under the selected story")

        elif state or extra_fields:
            target_id = resolve_task_reference(text, selected_story_tasks)
            if target_id:
                actions.append(
                    {
                        "action": "update_work_item",
                        "work_item_id": target_id,
                        "state": state,
                        "extra_fields": extra_fields,
                    }
                )
                reply_parts.append(f"update task {target_id}")

    if actions:
        return {
            "reply": "I will " + ", then ".join(reply_parts) + ".",
            "actions": actions,
        }

    return {
        "reply": (
            "I could not map that request safely. Select a user story, then ask me to show tasks, create tasks, "
            "update one task, or update all tasks in that story."
        ),
        "actions": [],
    }


def create_plan_with_openai(
    openai_client: AzureOpenAI | None,
    deployment: str,
    user_message: str,
    selected_story_id: int | None,
    selected_story_label: str,
    selected_story_item: dict[str, Any] | None,
    selected_story_tasks: list[dict[str, Any]],
    user_email: str,
) -> dict[str, Any]:
    if openai_client is None or not deployment:
        return fallback_plan(user_message, selected_story_id, selected_story_item, selected_story_tasks, user_email)

    prompt = {
        "selected_story_id": selected_story_id,
        "selected_story_label": selected_story_label,
        "selected_story": story_context(selected_story_item),
        "user_email": user_email,
        "selected_story_tasks": recent_context(selected_story_tasks),
        "supported_actions": {
            "list_story_tasks": {"fields": ["parent_id"]},
            "create_tasks": {
                "fields": ["parent_id", "assigned_to", "tasks"],
                "tasks_item_fields": ["title", "description"],
            },
            "update_selected_story": {
                "fields": ["work_item_id", "state", "title", "extra_fields"],
            },
            "update_work_item": {
                "fields": ["work_item_id", "state", "assigned_to", "title", "extra_fields"],
            },
            "bulk_update_story_tasks": {"fields": ["parent_id", "state", "extra_fields"]},
            "add_comment": {
                "fields": ["work_item_id", "comment"],
            },
        },
        "allowed_extra_fields": list(FIELD_MAP.keys()),
        "rules": [
            "Return valid JSON only.",
            "Use selected_story_id as the default parent when the user says 'this story' or does not specify a parent.",
            "If the user asks for multiple things in one message, return multiple actions in execution order.",
            "When creating tasks from the selected story, use the selected story title and description as context for titles and descriptions.",
            "If the user asks to update user story progress or the story state, use update_selected_story for the selected story.",
            "Do not send task scheduling fields like original_estimate, completed_work, or remaining_work on update_selected_story.",
            "When the user asks to show tasks for the selected story, use list_story_tasks.",
            "For 'create N tasks', generate N practical task titles and short descriptions tied to the selected story context.",
            "Map hours updates to remaining_work, completed_work, or original_estimate only.",
            "Map date updates to start_date or finish_date only. Treat 'actual date' as finish_date.",
            "Use bulk_update_story_tasks when the user asks to update all or every task in the selected story.",
            "Put action parameters directly on each action object. Do not wrap them inside a nested 'fields' object.",
            "Do not collapse separate requests into one action when they are logically separate.",
            "If the request is ambiguous, return no actions and explain what is missing in reply.",
        ],
        "required_output_shape": {
            "reply": "short assistant message",
            "actions": [
                {
                    "action": "list_story_tasks | create_tasks | update_work_item | bulk_update_story_tasks | add_comment",
                }
            ],
        },
        "user_request": user_message,
    }

    response = openai_client.chat.completions.create(
        model=deployment,
        response_format={"type": "json_object"},
        temperature=0.1,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are CHEETAH, an Azure DevOps operations planner. "
                    "Convert the user's request into an action plan JSON object."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(prompt),
            },
        ],
    )
    content = response.choices[0].message.content or "{}"
    return json.loads(content)


def execute_plan(
    client: AzureDevOpsClient,
    plan: dict[str, Any],
    default_user_email: str,
    selected_story_id: int | None,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    actions = plan.get("actions", [])
    result_lines = []
    affected_items: list[dict[str, Any]] = []
    created_or_loaded_rows: list[dict[str, Any]] = []
    refreshed_story_tasks: list[dict[str, Any]] = []

    for action in actions:
        action = normalize_action(action)
        action_name = action.get("action")

        if action_name == "list_story_tasks":
            story_id = action.get("parent_id") or selected_story_id
            refreshed_story_tasks = load_story_tasks(client, story_id)
            affected_items = refreshed_story_tasks
            created_or_loaded_rows.extend(normalize_work_items(refreshed_story_tasks))
            result_lines.append(f"Loaded {len(refreshed_story_tasks)} task(s) under story {story_id}.")
            continue

        if action_name == "create_tasks":
            parent_id = action.get("parent_id") or selected_story_id
            assigned_to = action.get("assigned_to") or default_user_email
            created_items = []
            for task in action.get("tasks", []):
                created = client.create_task(
                    title=task.get("title", "New task"),
                    description=task.get("description", ""),
                    assigned_to=assigned_to,
                    parent_id=parent_id,
                )
                created_items.append(created)
            refreshed_story_tasks = load_story_tasks(client, parent_id)
            created_or_loaded_rows.extend(normalize_work_items(created_items))
            affected_items = created_items
            result_lines.append(f"Created {len(created_items)} task(s) under story {parent_id}.")
            continue

        if action_name == "update_selected_story":
            work_item_id = int(action.get("work_item_id") or selected_story_id)
            client.update_work_item(
                work_item_id,
                state=action.get("state"),
                title=action.get("title"),
                extra_fields=map_extra_fields(action.get("extra_fields")),
            )
            updated_story = client.get_work_items([work_item_id])
            affected_items.extend(updated_story)
            created_or_loaded_rows.extend(normalize_work_items(updated_story))
            result_lines.append(f"Updated selected story {work_item_id}.")
            continue

        if action_name == "update_work_item":
            work_item_id = int(action["work_item_id"])
            client.update_work_item(
                work_item_id,
                state=action.get("state"),
                assigned_to=action.get("assigned_to"),
                title=action.get("title"),
                extra_fields=map_extra_fields(action.get("extra_fields")),
            )
            updated = client.get_work_items([work_item_id])
            affected_items.extend(updated)
            created_or_loaded_rows.extend(normalize_work_items(updated))
            result_lines.append(f"Updated work item {work_item_id}.")
            continue

        if action_name == "bulk_update_story_tasks":
            parent_id = action.get("parent_id") or selected_story_id
            task_items = load_story_tasks(client, parent_id)
            for item in task_items:
                task_id = item.get("fields", {}).get("System.Id")
                if task_id is None:
                    continue
                client.update_work_item(
                    int(task_id),
                    state=action.get("state"),
                    extra_fields=map_extra_fields(action.get("extra_fields")),
                )
            refreshed_story_tasks = load_story_tasks(client, parent_id)
            affected_items = refreshed_story_tasks
            created_or_loaded_rows.extend(normalize_work_items(refreshed_story_tasks))
            result_lines.append(f"Updated {len(refreshed_story_tasks)} task(s) under story {parent_id}.")
            continue

        if action_name == "add_comment":
            work_item_id = int(action["work_item_id"])
            client.add_comment(work_item_id, action.get("comment", ""))
            updated = client.get_work_items([work_item_id])
            affected_items.extend(updated)
            created_or_loaded_rows.extend(normalize_work_items(updated))
            result_lines.append(f"Added comment to work item {work_item_id}.")
            continue

    summary = plan.get("reply", "Done.")
    if result_lines:
        summary = f"{summary}\n\n" + "\n".join(f"- {line}" for line in result_lines)
    return summary, affected_items, created_or_loaded_rows, refreshed_story_tasks


def render_chat_history() -> None:
    for message in st.session_state["messages"]:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message.get("table"):
                st.dataframe(message["table"], use_container_width=True, hide_index=True)


st.set_page_config(page_title="CHEETAH", layout="wide")
st.title("CHEETAH")
st.caption("Select one of your user stories, then chat with CHEETAH to create tasks and update task states, hours, and dates.")

init_session_state()

client = build_azure_devops_client()
if client is None:
    st.error("Missing Azure DevOps settings. Add `AZDO_ORG_URL`, `AZDO_PROJECT`, and `AZDO_PAT` to `.env`.")
    st.stop()

openai_client = build_openai_client()
openai_deployment = get_env("AZURE_OPENAI_DEPLOYMENT")

if openai_client is None or not openai_deployment:
    st.warning(
        "Azure OpenAI is not fully configured. CHEETAH will fall back to basic command parsing until the Azure OpenAI settings are complete."
    )

with st.sidebar:
    st.subheader("Configuration")
    st.text_input("Azure DevOps org URL", value=get_env("AZDO_ORG_URL"), disabled=True)
    st.text_input("Project", value=get_env("AZDO_PROJECT"), disabled=True)
    user_email = st.text_input("Azure DevOps user email", value=DEFAULT_USER_EMAIL)
    access_allowed = is_access_allowed(user_email)

    if not access_allowed:
        st.warning("I ONLY WORK FOR LIONS , TO ACCESS SAY THE PHRASE")
        access_phrase = st.text_input("Access phrase", type="password")
        if access_phrase == ACCESS_PHRASE:
            st.session_state["access_granted"] = True
            st.session_state["access_email"] = user_email.strip().lower()
            access_allowed = True
            st.success("Access granted.")

    if st.button("Load my user stories", type="primary", disabled=not access_allowed):
        try:
            stories = load_assigned_user_stories(client, user_email)
            st.session_state["story_options"] = stories
            if stories:
                first_story = stories[0]
                first_story_id = first_story.get("fields", {}).get("System.Id")
                st.session_state["selected_story_id"] = first_story_id
                st.session_state["selected_story_label"] = story_label(first_story)
                st.session_state["selected_story_tasks"] = load_story_tasks(client, first_story_id)
                st.session_state["active_work_item_id"] = first_story_id
            else:
                st.session_state["selected_story_id"] = None
                st.session_state["selected_story_label"] = ""
                st.session_state["selected_story_tasks"] = []
        except Exception as exc:
            st.error(f"Failed to load user stories: {exc}")

    story_options = st.session_state.get("story_options", [])
    if story_options:
        story_map = {story_label(item): item for item in story_options}
        labels = list(story_map.keys())
        current_label = st.session_state.get("selected_story_label") or labels[0]
        selected_label = st.selectbox(
            "Select user story",
            labels,
            index=labels.index(current_label) if current_label in labels else 0,
        )
        selected_story = story_map[selected_label]
        selected_story_id = selected_story.get("fields", {}).get("System.Id")
        if selected_label != st.session_state.get("selected_story_label"):
            st.session_state["selected_story_label"] = selected_label
            st.session_state["selected_story_id"] = selected_story_id
            st.session_state["selected_story_tasks"] = load_story_tasks(client, selected_story_id)
            st.session_state["active_work_item_id"] = selected_story_id
    else:
        selected_story_id = None
        st.caption("Load your assigned user stories to set the working context.")

    st.markdown("**Examples**")
    st.code(
        "Show tasks in this story\n"
        "Create 5 tasks for regression testing\n"
        "Move all tasks in this story to Active\n"
        "Set remaining work to 2 hours for task 12345\n"
        "Set actual date to 2026-03-25 for every task in this story",
        language="text",
    )
    st.info("CHEETAH executes valid actions immediately. Keep `.env` local and rotate any token that was pasted into chat.")

selected_story_id = st.session_state.get("selected_story_id")
selected_story_label = st.session_state.get("selected_story_label", "")
selected_story_tasks = st.session_state.get("selected_story_tasks", [])
selected_story_item = get_story_item_from_options(selected_story_id)

if selected_story_label and access_allowed:
    st.subheader("Selected User Story")
    st.write(selected_story_label)
    story_description = (selected_story_item or {}).get("fields", {}).get("System.Description")
    if story_description:
        with st.expander("Story Description", expanded=False):
            st.markdown(story_description)
    if selected_story_tasks:
        st.dataframe(normalize_work_items(selected_story_tasks), use_container_width=True, hide_index=True)
    else:
        st.caption("No child tasks loaded for the selected story.")

render_chat_history()

user_prompt = st.chat_input(
    "Tell CHEETAH what to do in the selected user story",
    disabled=not access_allowed,
)
if user_prompt:
    st.session_state["messages"].append({"role": "user", "content": user_prompt})

    with st.chat_message("assistant"):
        with st.status("🐯💭 CHEETAH IS DOING RELAXX", expanded=True) as status:
            try:
                plan = create_plan_with_openai(
                    openai_client=openai_client,
                    deployment=openai_deployment,
                    user_message=user_prompt,
                    selected_story_id=selected_story_id,
                    selected_story_label=selected_story_label,
                    selected_story_item=selected_story_item,
                    selected_story_tasks=selected_story_tasks,
                    user_email=user_email,
                )
                reply, affected_items, table_rows, refreshed_story_tasks = execute_plan(client, plan, user_email, selected_story_id)

                if refreshed_story_tasks:
                    st.session_state["selected_story_tasks"] = refreshed_story_tasks
                    st.session_state["work_items"] = refreshed_story_tasks
                elif affected_items:
                    st.session_state["work_items"] = affected_items

                status.update(label="Done", state="complete")
                assistant_message: dict[str, Any] = {"role": "assistant", "content": reply}
                if table_rows:
                    assistant_message["table"] = table_rows
                st.session_state["messages"].append(assistant_message)
            except Exception as exc:
                status.update(label="MAA KAA BHOSRAAA AHHHHH", state="error")
                st.session_state["messages"].append(
                    {
                        "role": "assistant",
                        "content": f"MAA KAA BHOSRAAA AHHHHH\n\nAzure DevOps error:\n```text\n{exc}\n```",
                    }
                )

    st.rerun()
"""
