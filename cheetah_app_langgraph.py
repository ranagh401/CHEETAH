import os

import streamlit as st
from dotenv import load_dotenv

from azure_devops_client import AzureDevOpsClient
from graph_agent import CheetahAgentError, CheetahLangGraphAgent, build_langgraph_llm, normalize_work_items


load_dotenv()

DEFAULT_USER_EMAIL = os.getenv("AZDO_USER_EMAIL", "").strip()
PRIMARY_ALLOWED_EMAIL = "pratham.rana@polestaranalytics.com"
ACCESS_PHRASE = "ranagoat"


def get_secret(name: str, default: str = "") -> str:
    if name in st.secrets:
        value = st.secrets[name]
        return str(value).strip()
    return os.getenv(name, default).strip()


def build_client() -> AzureDevOpsClient | None:
    org_url = get_secret("AZDO_ORG_URL")
    project = get_secret("AZDO_PROJECT")
    pat = get_secret("AZDO_PAT")
    if not all([org_url, project, pat]):
        return None
    return AzureDevOpsClient(org_url=org_url, project=project, pat=pat)


def build_agent(client: AzureDevOpsClient) -> CheetahLangGraphAgent:
    llm = build_langgraph_llm(
        endpoint=get_secret("AZURE_OPENAI_ENDPOINT"),
        api_key=get_secret("AZURE_OPENAI_API_KEY"),
        api_version=get_secret("AZURE_OPENAI_API_VERSION"),
        deployment=get_secret("AZURE_OPENAI_DEPLOYMENT"),
    )
    return CheetahLangGraphAgent(client=client, llm=llm)


def load_assigned_user_stories(client: AzureDevOpsClient, user_email: str) -> list[dict]:
    ids = client.query_assigned_user_stories(user_email=user_email)
    return client.get_work_items(ids)


def load_story_tasks(client: AzureDevOpsClient, story_id: int | None) -> list[dict]:
    if not story_id:
        return []
    ids = client.query_child_tasks(parent_id=story_id)
    return client.get_work_items(ids)


def story_label(item: dict) -> str:
    fields = item.get("fields", {})
    return f"{fields.get('System.Id')} | {fields.get('System.Title')} | {fields.get('System.State')}"


def get_story_item_from_options(story_id: int | None) -> dict | None:
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
    return st.session_state.get("access_granted") and st.session_state.get("access_email") == normalized_email


def init_session_state() -> None:
    st.session_state.setdefault(
        "messages",
        [
            {
                "role": "assistant",
                "content": (
                    "I am CHEETAH with a LangGraph workflow. Select one of your user stories, then tell me what to do there.\n"
                    "- Show tasks in this story\n"
                    "- Create 4 tasks by reading the story description\n"
                    "- Update the third task to Active and set remaining work to 4 hours\n"
                    "- Update user story progress to Active"
                ),
            }
        ],
    )
    st.session_state.setdefault("story_options", [])
    st.session_state.setdefault("selected_story_id", None)
    st.session_state.setdefault("selected_story_label", "")
    st.session_state.setdefault("selected_story_tasks", [])
    st.session_state.setdefault("access_granted", False)
    st.session_state.setdefault("access_email", "")


def render_chat_history() -> None:
    for message in st.session_state["messages"]:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message.get("table"):
                st.dataframe(message["table"], use_container_width=True, hide_index=True)


def main() -> None:
    st.set_page_config(page_title="CHEETAH", layout="wide")
    st.title("CHEETAH")
    st.caption("LangGraph-powered Azure DevOps assistant with conversation memory, retries, and tool execution.")

    init_session_state()

    client = build_client()
    if client is None:
        st.error("Missing Azure DevOps settings. Add `AZDO_ORG_URL`, `AZDO_PROJECT`, and `AZDO_PAT` to `.env`.")
        st.stop()

    agent = build_agent(client)

    with st.sidebar:
        st.subheader("Configuration")
        st.text_input("Azure DevOps org URL", value=get_secret("AZDO_ORG_URL"), disabled=True)
        st.text_input("Project", value=get_secret("AZDO_PROJECT"), disabled=True)
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
                disabled=not access_allowed,
            )
            selected_story = story_map[selected_label]
            selected_story_id = selected_story.get("fields", {}).get("System.Id")
            if selected_label != st.session_state.get("selected_story_label"):
                st.session_state["selected_story_label"] = selected_label
                st.session_state["selected_story_id"] = selected_story_id
                st.session_state["selected_story_tasks"] = load_story_tasks(client, selected_story_id)
        else:
            st.caption("Load your assigned user stories to set the working context.")

        st.markdown("**Examples**")
        st.code(
            "Create 4 tasks by reading user story description and update user story progress to Active\n"
            "Update the third task to Active and set completed work to 16 hours\n"
            "Show tasks in this story and add comment to first task: started work",
            language="text",
        )

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

    user_prompt = st.chat_input("Tell CHEETAH what to do in the selected user story", disabled=not access_allowed)
    if user_prompt:
        st.session_state["messages"].append({"role": "user", "content": user_prompt})
        conversation_history = [
            {"role": message["role"], "content": message["content"]}
            for message in st.session_state["messages"]
        ]

        with st.chat_message("assistant"):
            with st.status("🐯💭 CHEETAH IS DOING RELAXX", expanded=True) as status:
                progress_lines: list[str] = []
                progress_placeholder = st.empty()

                def on_progress(text: str) -> None:
                    progress_lines.append(f"- {text}")
                    progress_placeholder.markdown("\n".join(progress_lines))

                try:
                    result = agent.invoke_turn(
                        user_request=user_prompt,
                        user_email=user_email,
                        selected_story_id=selected_story_id,
                        selected_story=selected_story_item,
                        selected_story_tasks=selected_story_tasks,
                        conversation_history=conversation_history,
                        progress_callback=on_progress,
                    )
                    if result["refreshed_story_tasks"]:
                        st.session_state["selected_story_tasks"] = result["refreshed_story_tasks"]
                    status.update(label="Done", state="complete")
                    assistant_message = {"role": "assistant", "content": result["assistant_message"]}
                    if result["rows"]:
                        assistant_message["table"] = result["rows"]
                    st.session_state["messages"].append(assistant_message)
                except CheetahAgentError as exc:
                    status.update(label="MAA KAA BHOSRAAA AHHHHH", state="error")
                    st.session_state["messages"].append(
                        {
                            "role": "assistant",
                            "content": f"MAA KAA BHOSRAAA AHHHHH\n\n{exc.source} error:\n```text\n{exc.detail}\n```",
                        }
                    )
                except Exception as exc:
                    status.update(label="MAA KAA BHOSRAAA AHHHHH", state="error")
                    st.session_state["messages"].append(
                        {
                            "role": "assistant",
                            "content": f"MAA KAA BHOSRAAA AHHHHH\n\nUnhandled app error:\n```text\n{type(exc).__name__}: {exc}\n```",
                        }
                    )
        st.rerun()


if __name__ == "__main__":
    main()
