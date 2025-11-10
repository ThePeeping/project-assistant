from typing import Dict, Any, List, Optional
from notion_client import Client
from datetime import datetime, timedelta
import httpx  # make sure it's in requirements.txt

TITLE_PROPERTY = "Title"
NOTES_PROPERTY_NAMES = {"Notes or Description", "Notes"}


def _slugify(name: str) -> str:
    return "_".join(name.lower().split())


def _rich_text_to_plain(items: List[Dict[str, Any]]) -> Optional[str]:
    if not items:
        return None
    text = "".join(part.get("plain_text") or "" for part in items).strip()
    return text or None

ACTIVE_STATUS_FALLBACKS = [
    "Not started",
    "In progress",
    "On hold",
    "Blocked",
    "To Do",
    "In Progress",
]
COMPLETE_GROUP_NAMES = {"complete", "completed", "done"}


class NotionHelper:
    def __init__(self, client: Client, database_id: str, notion_token: str):
        self.client = client
        self.database_id = database_id
        self.notion_token = notion_token  # used by HTTP fallback
        self._schema_cache: Optional[Dict[str, Any]] = None

    # ---------- SDK + HTTP fallback ----------
    def _query_db(self, **kwargs) -> Dict[str, Any]:
        """
        Prefer notion-client's databases.query(...).
        If it's unavailable (environment mismatch), fall back to direct HTTP POST.
        """
        db = getattr(self.client, "databases", None)

        # Try official SDK method (expected by current releases)
        if db is not None:
            sdk_query = getattr(db, "query", None)
            if callable(sdk_query):
                return sdk_query(**kwargs)

        # Fallback: raw HTTP call to Notion REST API
        url = f"https://api.notion.com/v1/databases/{kwargs['database_id']}/query"
        # Remove database_id from JSON payload for the REST body
        body = {k: v for k, v in kwargs.items() if k != "database_id"}
        headers = {
            "Authorization": f"Bearer {self.notion_token}",
            # Any current Notion-Version works for simple queries. This one is stable.
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=30.0) as http:
            r = http.post(url, headers=headers, json=body)
            r.raise_for_status()
            return r.json()

    # ---------- Schema ----------
    def _fetch_schema_via_sdk(self) -> Dict[str, Any]:
        try:
            db = self.client.databases.retrieve(self.database_id)
            return db.get("properties", {}) or {}
        except Exception:
            return {}

    def _fetch_schema_via_http(self) -> Dict[str, Any]:
        url = f"https://api.notion.com/v1/databases/{self.database_id}"
        headers = {
            "Authorization": f"Bearer {self.notion_token}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=30.0) as http:
                r = http.get(url, headers=headers)
                r.raise_for_status()
                data = r.json()
                return data.get("properties", {}) or {}
        except Exception:
            return {}

    def schema(self) -> Dict[str, Any]:
        if self._schema_cache is not None:
            return self._schema_cache

        props = self._fetch_schema_via_sdk()
        if not props:
            props = self._fetch_schema_via_http()

        self._schema_cache = props or {}
        return self._schema_cache

    def refresh_schema(self):
        self._schema_cache = None
        return self.schema()

    # ---------- Fetch ----------
    def _active_tasks_query(self) -> tuple[Dict[str, Any], List[str]]:
        active_statuses = self._active_status_names()
        query_kwargs: Dict[str, Any] = {
            "database_id": self.database_id,
            "sorts": [{"property": "Due Date", "direction": "ascending"}],
        }
        if active_statuses:
            query_kwargs["filter"] = {
                "or": [self._status_filter(name) for name in active_statuses]
            }
        return query_kwargs, active_statuses

    def list_active_tasks(self) -> List[Dict[str, Any]]:
        """Return non-completed tasks ordered by due date."""
        query_kwargs, active_statuses = self._active_tasks_query()

        resp = self._query_db(**query_kwargs)
        tasks = [self._page_to_task(p) for p in resp.get("results", [])]

        if not active_statuses:
            tasks = [
                t
                for t in tasks
                if (t.get("status") or "").strip().lower()
                not in COMPLETE_GROUP_NAMES
            ]

        return tasks

    def list_active_task_pages(self) -> List[Dict[str, Any]]:
        """Return raw Notion pages for the active-task query."""
        query_kwargs, active_statuses = self._active_tasks_query()
        try:
            resp = self._query_db(**query_kwargs)
            results = resp.get("results", [])
            if not active_statuses:
                filtered = []
                for page in results:
                    task_data = self._page_to_task(page) or {}
                    status = (task_data.get("status") or "").strip().lower()
                    if status not in COMPLETE_GROUP_NAMES:
                        filtered.append(page)
                return filtered
            return results
        except Exception:
            return []

    def list_completed_in_range(self, days: int = 7) -> List[Dict[str, Any]]:
        since = (datetime.utcnow() - timedelta(days=days)).isoformat()
        resp = self._query_db(
            database_id=self.database_id,
            filter={
                "and": [
                    self._status_filter("Done"),
                    {"timestamp": "last_edited_time", "last_edited_time": {"on_or_after": since}},
                ]
            },
            sorts=[{"timestamp": "last_edited_time", "direction": "descending"}],
        )
        return [self._page_to_task(p) for p in resp.get("results", [])]

    def _page_to_task(self, page: Dict[str, Any]) -> Dict[str, Any]:
        schema = self.schema()
        props = page.get("properties", {})
        task: Dict[str, Any] = {
            "id": page.get("id"),
            "last_edited_time": page.get("last_edited_time"),
            "properties": {},
            "_raw_properties": props,
        }

        for prop_name, prop_schema in schema.items():
            raw_value = props.get(prop_name)
            value = self._extract_property_value(prop_name, prop_schema, raw_value)
            task["properties"][prop_name] = value

            slug = _slugify(prop_name)
            if slug:
                task[slug] = value

            if prop_schema.get("type") == "title":
                task["title"] = value or "Untitled"

            if prop_name in NOTES_PROPERTY_NAMES and value is not None:
                task["notes"] = value

        # Fallback: add properties from payload even if missing in schema
        for prop_name, raw_payload in props.items():
            if prop_name in task["properties"]:
                continue
            pseudo_schema = {"type": raw_payload.get("type")}
            value = self._extract_property_value(prop_name, pseudo_schema, raw_payload)
            task["properties"][prop_name] = value
            slug = _slugify(prop_name)
            if slug and slug not in task:
                task[slug] = value
            if raw_payload.get("type") == "title" and value:
                task["title"] = value

        task.setdefault("title", "Untitled")

        # Backwards compatibility with earlier keys
        if "task" in task and "task_text" not in task:
            task["task_text"] = task["task"]

        return task

    # ---------- Create / Delete ----------
    def create_task(self, title: str, defaults: Optional[Dict[str, Any]] = None) -> str:
        if not title.strip():
            raise ValueError("Title cannot be empty")
        properties: Dict[str, Any] = {
            TITLE_PROPERTY: {"title": [{"text": {"content": title}}]}
        }
        defaults = defaults or {}
        for k, v in defaults.items():
            properties[k] = self._value_for_property(k, v)
        page = self.client.pages.create(
            parent={"database_id": self.database_id},
            properties=properties,
        )
        return page["id"]

    def delete_task(self, page_id: str):
        self.client.pages.update(page_id=page_id, archived=True)

    # ---------- Update ----------
    def update_property(self, page_id: str, property_name: str, new_value: Any) -> None:
        schema = self.schema()
        if property_name not in schema:
            raise ValueError(f"Unknown property: {property_name}")
        notion_value = self._value_for_property(property_name, new_value)
        self.client.pages.update(page_id=page_id, properties={property_name: notion_value})

    def append_notes(self, page_id: str, text: str) -> None:
        page = self.client.pages.retrieve(page_id=page_id)
        props = page.get("properties", {})
        notes = props.get("Notes or Description", {})
        current = ""
        if notes.get("rich_text"):
            current = notes["rich_text"][0].get("plain_text", "")
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
        new_text = f"{current}\n\n[{timestamp}] {text}" if current else f"[{timestamp}] {text}"
        self.client.pages.update(
            page_id=page_id,
            properties={"Notes or Description": {"rich_text": [{"text": {"content": new_text[:2000]}}]}},
        )

    # ---------- Helpers ----------
    def _status_filter(self, value: str) -> Dict[str, Any]:
        """
        Build a status/select filter that matches the actual Notion schema.
        Supports both legacy select properties and the newer status type.
        """
        schema = self.schema()
        status_prop = schema.get("Status")
        if not status_prop:
            raise ValueError("Database is missing a 'Status' property.")

        prop_type = status_prop.get("type")
        if prop_type == "status":
            key = "status"
            payload = {"equals": value}
        elif prop_type == "select":
            key = "select"
            payload = {"equals": value}
        elif prop_type == "multi_select":
            key = "multi_select"
            payload = {"contains": value}
        else:
            raise ValueError(f"Unsupported Status property type: {prop_type}")

        return {"property": "Status", key: payload}

    def _active_status_names(self) -> List[str]:
        """
        Determine which Status options should be considered "active" by
        excluding groups named like Complete/Done when available.
        """
        schema = self.schema()
        status_prop = schema.get("Status")
        if not status_prop:
            return []

        prop_type = status_prop.get("type")
        config = status_prop.get(prop_type, {}) or {}
        options = config.get("options") or []
        groups = config.get("groups") or []

        option_by_id = {
            opt.get("id"): opt.get("name")
            for opt in options
            if opt.get("id") and opt.get("name")
        }

        active_names: List[str] = []

        if groups:
            complete_names = {name.strip().lower() for name in COMPLETE_GROUP_NAMES}
            for group in groups:
                group_name = (group.get("name") or "").strip().lower()
                if group_name in complete_names:
                    continue
                for oid in group.get("option_ids") or []:
                    name = option_by_id.get(oid)
                    if name and name not in active_names:
                        active_names.append(name)

        if not active_names and options:
            available_names = {opt.get("name") for opt in options if opt.get("name")}
            for fallback in ACTIVE_STATUS_FALLBACKS:
                if fallback in available_names and fallback not in active_names:
                    active_names.append(fallback)

        if not active_names:
            for opt in options:
                name = opt.get("name")
                if not name:
                    continue
                if name.strip().lower() in COMPLETE_GROUP_NAMES:
                    continue
                if name not in active_names:
                    active_names.append(name)

        return active_names

    def _extract_property_value(
        self,
        prop_name: str,
        prop_schema: Dict[str, Any],
        prop_payload: Optional[Dict[str, Any]],
    ) -> Any:
        ptype = prop_schema.get("type")
        payload = prop_payload or {}

        if ptype == "title":
            return _rich_text_to_plain(payload.get("title", [])) or "Untitled"

        if ptype == "rich_text":
            return _rich_text_to_plain(payload.get("rich_text", []))

        if ptype == "select":
            selected = payload.get("select")
            return selected.get("name") if selected else None

        if ptype == "status":
            selected = payload.get("status")
            return selected.get("name") if selected else None

        if ptype == "multi_select":
            return [
                opt.get("name")
                for opt in payload.get("multi_select", [])
                if opt.get("name")
            ]

        if ptype == "number":
            return payload.get("number")

        if ptype == "date":
            date_obj = payload.get("date")
            return date_obj.get("start") if date_obj else None

        if ptype == "checkbox":
            return payload.get("checkbox")

        if ptype == "url":
            return payload.get("url")

        if ptype == "email":
            return payload.get("email")

        if ptype == "phone_number":
            return payload.get("phone_number")

        if ptype == "people":
            people = payload.get("people") or []
            return [
                person.get("name") or person.get("id")
                for person in people
                if person.get("name") or person.get("id")
            ]

        return None

    def _value_for_property(self, property_name: str, value: Any) -> Dict[str, Any]:
        schema = self.schema()
        prop_schema = schema.get(property_name)
        if not prop_schema:
            raise ValueError(f"Unknown property: {property_name}")

        prop_type = prop_schema.get("type")

        if prop_type == "title":
            content = "" if value is None else str(value)
            return {"title": [{"text": {"content": content}}]}

        if prop_type == "rich_text":
            content = "" if value is None else str(value)
            if not content:
                return {"rich_text": []}
            return {"rich_text": [{"text": {"content": content}}]}

        if prop_type == "number":
            if value in (None, ""):
                return {"number": None}
            try:
                return {"number": float(value)}
            except (TypeError, ValueError):
                return {"number": None}

        if prop_type == "date":
            if not value:
                return {"date": None}
            return {"date": {"start": str(value)}}

        if prop_type == "select":
            if not value:
                return {"select": None}
            return {"select": {"name": str(value)}}

        if prop_type == "status":
            if not value:
                return {"status": None}
            return {"status": {"name": str(value)}}

        if prop_type == "multi_select":
            if not value:
                return {"multi_select": []}
            if not isinstance(value, (list, tuple, set)):
                value = [value]
            return {"multi_select": [{"name": str(v)} for v in value if v not in (None, "")]}

        if prop_type == "checkbox":
            return {"checkbox": bool(value)}

        if prop_type == "url":
            return {"url": value or None}

        if prop_type == "email":
            return {"email": value or None}

        if prop_type == "phone_number":
            return {"phone_number": value or None}

        return {"rich_text": [] if value in (None, "") else [{"text": {"content": str(value)}}]}
