import logging
import streamlit as st
from anthropic import Anthropic
from notion_client import Client, APIResponseError
from supabase import create_client
from datetime import datetime, timedelta
import json

from modules.notion_utils import NotionHelper
from modules.ui_editor import render_dynamic_editor
from modules.assistant_tools import AssistantOps
from modules.logger import EventLogger
from modules.sync import setup_autorefresh


logger = logging.getLogger("project_assistant")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)


# ---------- Page config ----------
st.set_page_config(page_title="Project Assistant", page_icon="🚀", layout="wide")


# ---------- Notion SDK 2.7.0+ compatibility (data_sources) ----------
# Provides databases.query compatibility by routing to data_sources.query.
_DSID_CACHE: dict[str, str] = {}


def _get_first_data_source_id(notion_client: Client, database_id: str) -> str:
    if database_id in _DSID_CACHE:
        logger.debug("Notion data_source_id cache hit for %s", database_id)  # DEV-LOG
        return _DSID_CACHE[database_id]
    db = notion_client.databases.retrieve(database_id=database_id)
    data_sources = db.get("data_sources") or []
    logger.info(
        "Retrieved %d data_sources for database %s",
        len(data_sources),
        database_id,
    )
    if not data_sources:
        raise RuntimeError(
            "No data_sources found for this database. Open it in Notion and ensure it has at least one data source."
        )
    dsid = data_sources[0]["id"]
    _DSID_CACHE[database_id] = dsid
    logger.info("Using data_source_id %s for database %s", dsid, database_id)
    return dsid


def _enable_notion_datasource_compat(notion_client: Client) -> None:
    """
    If the SDK exposes data_sources, add a shim so
    notion_client.databases.query(...) keeps working.
    """
    if hasattr(notion_client, "data_sources"):
        logger.info("Enabling data_sources shim for Notion client")

        def _db_query(*, database_id: str = None, **kwargs):
            if not database_id:
                raise TypeError("databases.query requires database_id")
            dsid = _get_first_data_source_id(notion_client, database_id)
            return notion_client.data_sources.query(data_source_id=dsid, **kwargs)

        setattr(notion_client.databases, "query", _db_query)
        if not hasattr(notion_client.databases, "query_database"):
            setattr(notion_client.databases, "query_database", _db_query)
    else:
        logger.warning(
            "Notion client does not expose data_sources; using default databases.query"
        )


# ---------- Clients ----------
@st.cache_resource
def init_clients():
    logger.info("Initializing external clients")
    anthropic = Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])
    logger.info("Anthropic client initialized")

    notion = Client(auth=st.secrets["NOTION_TOKEN"])
    _enable_notion_datasource_compat(notion)
    logger.info("Notion client initialized")

    supabase = None
    try:
        supabase = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
        logger.info("Supabase client initialized")
    except Exception:
        # Supabase is optional for logging, keep running if not configured
        logger.warning(
            "Supabase client could not be initialized; console logs only",
            exc_info=True,
        )
        pass
    return anthropic, notion, supabase


ANTHROPIC, NOTION, SUPABASE = init_clients()
NOTION_DB_ID = st.secrets["NOTION_DATABASE_ID"]

# ---------- App services ----------
# Your NotionHelper requires a notion_token, pass it explicitly
NOTION_HELPER = NotionHelper(NOTION, NOTION_DB_ID, st.secrets["NOTION_TOKEN"])
ASSIST_OPS = AssistantOps(ANTHROPIC)
LOGGER = EventLogger(SUPABASE)

AUTH_USERNAME = "assiomar"
AUTH_PASSWORD = "wBmTt$Wcf3poo@ZEX$"

# ---------- Auth ----------
if not st.session_state.get("auth_ok"):
    st.title("Login")
    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign In")

    if submitted:
        if username == AUTH_USERNAME and password == AUTH_PASSWORD:
            st.session_state.auth_ok = True
            st.session_state.user_id = username
            st.rerun()
        else:
            st.error("Invalid username or password")
    st.stop()

USER_ID = st.session_state.get("user_id", AUTH_USERNAME)
ADMINS = set([x.strip() for x in st.secrets.get("ADMINS", "").split(",") if x.strip()])
IS_ADMIN = (not ADMINS) or (USER_ID in ADMINS)

# ---------- Sidebar: controls ----------
with st.sidebar:
    st.caption(f"User: {USER_ID} {'[admin]' if IS_ADMIN else ''}")
    setup_autorefresh(seconds=120)

    if st.button("🔄 Refresh tasks"):
        logger.info("Manual task refresh triggered by %s", USER_ID)
        logger.debug("Clearing cached tasks before schema refresh")  # DEV-LOG
        st.session_state.pop("tasks", None)
        NOTION_HELPER.refresh_schema()
        st.rerun()

# ---------- Load tasks ----------
@st.cache_data(ttl=60)
def fetch_active_tasks():
    try:
        tasks = NOTION_HELPER.list_active_tasks()
        logger.info(
            "Fetched %d active tasks from Notion database %s",
            len(tasks),
            NOTION_DB_ID,
        )
        return tasks
    except Exception:
        logger.exception(
            "Failed to fetch tasks from Notion database %s", NOTION_DB_ID
        )
        raise

if "tasks" not in st.session_state:
    st.session_state.tasks = fetch_active_tasks()
    logger.debug("Cached tasks loaded into session_state")  # DEV-LOG

# ---------- Header ----------
st.title("🚀 AI Project Assistant")
st.caption("Conversational updates, manual editing, and weekly reporting")

# ---------- Create / Delete ----------
with st.sidebar.expander("➕ Create Task", expanded=False):
    new_title = st.text_input("Title", key="new_title")
    default_status = st.selectbox("Status", ["To Do", "In Progress", "Done", "Blocked"], key="new_status")
    default_due = st.date_input("Due Date", value=datetime.now().date(), key="new_due")
    if st.button("Create", disabled=not IS_ADMIN):
        try:
            logger.info(
                "User %s creating task '%s' (status=%s, due=%s)",
                USER_ID,
                new_title,
                default_status,
                default_due,
            )
            page_id = NOTION_HELPER.create_task(
                new_title,
                defaults={"Status": default_status, "Due Date": str(default_due)},
            )
            LOGGER.log("create_task", USER_ID, {"title": new_title, "page_id": page_id})
            logger.info("Task '%s' created with page_id=%s", new_title, page_id)
            st.success("Created")
            st.session_state.tasks = fetch_active_tasks()
            st.rerun()
        except Exception as e:
            logger.exception(
                "Failed to create task '%s' for user %s", new_title, USER_ID
            )
            st.warning(f"Could not create: {e}")

with st.sidebar.expander("🗑️ Delete Task", expanded=False):
    titles = [t["title"] for t in st.session_state.tasks] or [""]
    del_sel = st.selectbox("Select", titles)
    if st.button("Archive", disabled=not IS_ADMIN):
        try:
            task = next((t for t in st.session_state.tasks if t["title"] == del_sel), None)
            if task:
                logger.info(
                    "User %s archiving task '%s' (page_id=%s)",
                    USER_ID,
                    del_sel,
                    task["id"],
                )
                NOTION_HELPER.delete_task(task["id"])
                LOGGER.log("delete_task", USER_ID, {"title": del_sel, "page_id": task["id"]})
                logger.info("Task '%s' archived", del_sel)
                st.success("Archived in Notion")
                st.session_state.tasks = fetch_active_tasks()
                st.rerun()
        except Exception as e:
            logger.exception(
                "Failed to archive task '%s' for user %s", del_sel, USER_ID
            )
            st.warning(f"Could not archive: {e}")

# ---------- Tasks list ----------
col_left, col_right = st.columns([1, 2])

with col_left:
    st.subheader("Current Tasks")
    if not st.session_state.tasks:
        st.info("No active tasks found")
    else:
        status_icons = {"To Do": "○", "In Progress": "↻", "Done": "✓", "Blocked": "×"}
        for t in st.session_state.tasks[:10]:
            status_emoji = status_icons.get(t.get("status"), "•")
            st.markdown(f"**{status_emoji} {t['title']}**")
            line = []
            if t.get("due_date"): line.append(f"Due {t['due_date']}")
            if t.get("category"): line.append(f"{t['category']}")
            if t.get("phase"): line.append(f"{t['phase']}")
            if line:
                st.caption(" | ".join(line))
            st.divider()

# ---------- Dynamic editor with autosave toggle ----------
with col_right:
    st.subheader("Edit Panel")
    titles = [t["title"] for t in st.session_state.tasks]
    if titles:
        selected = st.selectbox("Pick a task", titles)
        selected_task = next(t for t in st.session_state.tasks if t["title"] == selected)

        if "autosave_enabled" not in st.session_state:
            st.session_state.autosave_enabled = False
        autosave = st.checkbox("Auto-save ✓", value=st.session_state.autosave_enabled, key="autosave_enabled")
        logger.debug(
            "Autosave set to %s for task '%s'",
            autosave,
            selected_task["title"],
        )  # DEV-LOG

        def on_change_log(prop, old, new):
            logger.info(
                "User %s updating task '%s' property '%s'",
                USER_ID,
                selected_task["title"],
                prop,
            )
            logger.debug("Property change %s: %s -> %s", prop, old, new)  # DEV-LOG
            LOGGER.log("update_property", USER_ID, {"task": selected_task["title"], "prop": prop, "old": old, "new": new})

        changed = render_dynamic_editor(NOTION_HELPER, selected_task, autosave, on_change_log)
        if changed:
            logger.info("Bulk update saved for task '%s'", selected_task["title"])
            LOGGER.log("bulk_update", USER_ID, {"task": selected_task["title"]})
            st.session_state.tasks = fetch_active_tasks()
            st.success("Saved changes")
    else:
        st.caption("Nothing to edit")

# ---------- Conversational area ----------
st.divider()
st.subheader("Chat Assistant")

if "messages" not in st.session_state:
    st.session_state.messages = [{"role": "assistant", "content": "Hi, I can update Notion for you. Try: Mark 'Landing page' as Done."}]

for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.write(m["content"])

prompt = st.chat_input("Type an instruction or question...")
if prompt:
    logger.info("Chat prompt from %s: %s", USER_ID, prompt)
    st.chat_message("user").write(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})

    lower = prompt.lower()
    logger.debug("Normalized chat prompt: %s", lower)  # DEV-LOG
    responded = False
    try:
        if lower.startswith("mark ") and " as done" in lower:
            title = prompt.split("Mark ")[-1].split(" as done")[0].strip("'\" ")
            logger.debug("Parsed chat title candidate: %s", title)  # DEV-LOG
            task = next((t for t in st.session_state.tasks if title.lower() in t["title"].lower()), None)
            if task:
                logger.info("Chat mark-as-done matched task '%s'", task["title"])
                NOTION_HELPER.update_property(task["id"], "Status", "Done")
                summary = ASSIST_OPS.summarize_completion(task["title"], task.get("notes") or "")
                NOTION_HELPER.append_notes(task["id"], summary)
                LOGGER.log("status_done", USER_ID, {"task": task["title"]})
                st.chat_message("assistant").write(f"Marked '{task['title']}' as Done and added a summary.")
                st.session_state.tasks = fetch_active_tasks()
                responded = True
            else:
                logger.warning(
                    "Chat mark-as-done request did not match any task for fragment '%s'",
                    title,
                )
        if not responded:
            st.chat_message("assistant").write("Got it. Use the editor on the right or say things like, Set due date of X to 2025-11-12.")
            logger.info("Chat prompt from %s routed to generic helper response", USER_ID)
    except Exception as e:
        logger.exception("Chat handler failed for prompt '%s'", prompt)
        st.chat_message("assistant").write(f"Could not process: {e}")

# ---------- Weekly report ----------
st.divider()
st.subheader("Weekly Report")
range_days = st.slider("Days to include", 7, 21, 7)
if st.button("Generate report"):
    logger.info("Weekly report requested by %s for last %d days", USER_ID, range_days)
    try:
        completed = NOTION_HELPER.list_completed_in_range(days=range_days)
        logger.debug(
            "Weekly report source tasks: %s",
            [t.get("title") for t in completed],
        )  # DEV-LOG
        report = ASSIST_OPS.weekly_report(completed)
        title = f"Weekly Report, generated {datetime.utcnow().strftime('%Y-%m-%d')}"
        NOTION_HELPER.create_task(title, defaults={"Notes or Description": report, "Status": "Done"})
        LOGGER.log("weekly_report", USER_ID, {"items": len(completed)})
        logger.info(
            "Weekly report '%s' created with %d completed items", title, len(completed)
        )
        st.success("Report created in Notion under your database as a new page")
    except Exception as e:
        logger.exception("Failed to generate weekly report for user %s", USER_ID)
        st.warning(f"Could not generate report: {e}")
