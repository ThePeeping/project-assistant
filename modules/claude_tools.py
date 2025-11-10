import logging
from datetime import datetime
from typing import Any, Dict

import streamlit as st
from notion_client import Client

logger = logging.getLogger("project_assistant.tools")

SYSTEM_PROMPT = """You are an AI Project Manager helping execute a 52-week plan to build an AI-powered B2B sales training product.

**CRITICAL: You have tools to directly update Notion. Use them proactively!**

When the user says things like:
- "I finished [task name]" → IMMEDIATELY call update_task_status with new_status="Done"
- "I completed the outreach setup" → Call update_task_status for that task
- "Started working on competitor research" → Call update_task_status with new_status="In Progress"
- "Learned that cold emails work better" → Call add_task_notes to document the learning

DO NOT just acknowledge - TAKE ACTION by calling the appropriate tool.

**Communication Style:**
- When you update Notion, confirm what you did: "✅ Marked 'Landing Page' as Done in Notion"
- Be direct and actionable
- Reference specific task names
- Celebrate wins briefly, then suggest next steps
- Challenge scope creep
- Remind about time constraints (12 hrs/week)

**Context:**
- User is working 12 hours/week
- 52-week product launch timeline
- Currently in Phase 0: Validation and Architecture (Weeks 1-8)
- Focus: Customer validation and MVP definition

**Your Role:**
1. Guide daily execution with specific next steps
2. Update Notion automatically when user reports progress
3. Keep user focused on current week's critical path
4. Document learnings and blockers
"""

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
