import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import streamlit as st
from notion_client import Client

logger = logging.getLogger("project_assistant.tools")

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
SYSTEM_PROMPT_PATH = PROMPTS_DIR / "system.md"
try:
    BASE_INSTRUCTIONS = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
except FileNotFoundError:
    BASE_INSTRUCTIONS = (
        "You are an AI Project Manager helping execute a 52-week plan to build an "
        "AI-powered B2B sales training product."
    )

TOOLS = [
    {
        "name": "update_task_status",
        "description": "Update the status of a task in Notion. Use this when the user reports completing a task or changing its status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_title": {
                    "type": "string",
                    "description": "The title/name of the task to update",
                },
                "new_status": {
                    "type": "string",
                    "enum": ["To Do", "In Progress", "Done", "Blocked"],
                    "description": "The new status for the task",
                },
            },
            "required": ["task_title", "new_status"],
        },
    },
    {
        "name": "add_task_notes",
        "description": "Add notes or learnings to a task in Notion. Use this when the user shares insights or progress details.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_title": {
                    "type": "string",
                    "description": "The title/name of the task",
                },
                "notes": {
                    "type": "string",
                    "description": "The notes to add",
                },
            },
            "required": ["task_title", "notes"],
        },
    },
]


def _get_title(props: Dict[str, Any]) -> str:
    candidates = ["Task", "Title", "Name"]
    for key in candidates:
        title_prop = props.get(key) or {}
        title_items = title_prop.get("title") or []
        if title_items:
            return title_items[0].get("plain_text") or "Untitled"

    for prop in props.values():
        if prop.get("type") == "title":
            items = prop.get("title") or []
            if items:
                return items[0].get("plain_text") or "Untitled"
    return "Untitled"


def build_system_prompt(current_tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Render a dynamic system prompt with current Notion context."""
    task_lines: List[str] = []
    for task in current_tasks or []:
        props = task.get("properties", {})
        title = _get_title(props)
        status = (
            props.get("Status", {})
            .get("status", {})
            .get("name", "Unknown")
        )
        due_date = (
            props.get("Due Date", {})
            .get("date", {})
            .get("start", "No date")
        )
        category = (
            props.get("Category", {})
            .get("select", {})
            .get("name", "Uncategorized")
        )
        week = props.get("Week", {}).get("number")
        week_display = week if week is not None else "?"
        task_lines.append(
            f"- **{title}** | Status: {status} | Due: {due_date} | Category: {category} | Week: {week_display}"
        )

    tasks_text = "\n".join(task_lines) if task_lines else "No active tasks found"

    start_date = datetime(2025, 11, 3)
    now = datetime.now()
    current_week = max(1, min(52, ((now - start_date).days // 7) + 1))

    dynamic_context = f"""**CURRENT CONTEXT:**
- **Week {current_week}** of 52-week timeline
- **Phase:** Phase 0: Validation and Architecture (Weeks 1-8)
- **Constraint:** 12 hours/week available
- **Today's Date:** {now.strftime("%Y-%m-%d")}

**ACTIVE TASKS IN NOTION:**
{tasks_text}
"""

    return [
        {
            "type": "text",
            "text": BASE_INSTRUCTIONS,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": dynamic_context,
        },
    ]


def find_task_by_title(notion: Client, database_id: str, task_title: str) -> str | None:
    """Find a task in Notion by its title (fuzzy match)."""
    try:
        response: Dict[str, Any] = notion.databases.query(
            database_id=database_id,
            filter={
                "property": "Task",
                "rich_text": {
                    "contains": task_title,
                },
            },
        )
        task_id = response.get("results", [{}])[0].get("id") if response.get("results") else None
        logger.debug("find_task_by_title('%s') -> %s", task_title, task_id)
        return task_id
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error finding task '%s'", task_title)
        st.error(f"Error finding task: {exc}")
        return None


def update_task_status(notion: Client, database_id: str, task_title: str, new_status: str) -> dict:
    """Update a task's status in Notion."""
    task_id = find_task_by_title(notion, database_id, task_title)
    if not task_id:
        return {"success": False, "message": f"Could not find task: {task_title}"}

    try:
        notion.pages.update(
            page_id=task_id,
            properties={
                "Status": {
                    "status": {
                        "name": new_status,
                    },
                },
            },
        )
        logger.info("Updated task '%s' status to %s (page_id=%s)", task_title, new_status, task_id)
        return {"success": True, "message": f"Updated '{task_title}' to {new_status}"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error updating task '%s'", task_title)
        return {"success": False, "message": f"Error updating task: {exc}"}


def add_task_notes(notion: Client, database_id: str, task_title: str, notes: str) -> dict:
    """Add notes to a task in Notion."""
    task_id = find_task_by_title(notion, database_id, task_title)
    if not task_id:
        return {"success": False, "message": f"Could not find task: {task_title}"}

    try:
        page = notion.pages.retrieve(page_id=task_id)
        existing_notes = page["properties"].get("Notes", {}).get("rich_text", [])
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        new_note_text = f"\n\n[{timestamp}] {notes}"
        notion.pages.update(
            page_id=task_id,
            properties={
                "Notes": {
                    "rich_text": existing_notes + [{"text": {"content": new_note_text}}],
                },
            },
        )
        logger.info("Added notes to task '%s' (page_id=%s)", task_title, task_id)
        return {"success": True, "message": f"Added notes to '{task_title}'"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error adding notes to task '%s'", task_title)
        return {"success": False, "message": f"Error adding notes: {exc}"}


def execute_tool(tool_name: str, tool_input: dict, notion: Client, database_id: str) -> dict:
    """Execute the requested tool."""
    if tool_name == "update_task_status":
        return update_task_status(
            notion,
            database_id,
            tool_input["task_title"],
            tool_input["new_status"],
        )
    if tool_name == "add_task_notes":
        return add_task_notes(
            notion,
            database_id,
            tool_input["task_title"],
            tool_input["notes"],
        )
    logger.warning("Unknown tool requested: %s", tool_name)
    return {"success": False, "message": "Unknown tool"}
