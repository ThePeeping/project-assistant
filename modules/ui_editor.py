import streamlit as st
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .notion_utils import NotionHelper, NOTES_PROPERTY_NAMES

# ----- UI helpers -----

def _badge(label: str, color_hex: Optional[str] = None):
    if not label:
        return
    if color_hex:
        st.sidebar.markdown(
            f"<span style='background:{color_hex}; color:#111; padding:2px 6px; border-radius:6px'>{label}</span>",
            unsafe_allow_html=True,
        )
    else:
        st.sidebar.caption(label)

# Map Notion color names to simple hex, fallback if unknown
COLOR_MAP = {
    "default": "#ddd", "gray": "#e2e2e2", "brown": "#d2b48c", "orange": "#ffd8a8",
    "yellow": "#fff3bf", "green": "#d3f9d8", "blue": "#d0ebff", "purple": "#e5dbff",
    "pink": "#ffdce5", "red": "#ffc9c9",
}

NONE_OPTION = "-- None --"
EDITABLE_TYPES = {"title", "rich_text", "select", "status", "multi_select", "number", "date"}
SECTION_RULES: List[Tuple[str, set[str]]] = [
    ("Task Info", {"title", "rich_text"}),
    ("Timeline", {"date", "number"}),
    ("Status & Tags", {"status", "select", "multi_select"}),
]


def _slugify(name: str) -> str:
    return "_".join(name.lower().split())


def _build_sections(schema: Dict[str, Any]) -> List[Tuple[str, List[str]]]:
    buckets: Dict[str, List[str]] = {section: [] for section, _ in SECTION_RULES}
    buckets["Other"] = []

    for prop_name, prop_info in schema.items():
        ptype = prop_info.get("type")
        if ptype not in EDITABLE_TYPES:
            continue
        assigned = False
        for section, allowed_types in SECTION_RULES:
            if ptype in allowed_types:
                buckets[section].append(prop_name)
                assigned = True
                break
        if not assigned:
            buckets["Other"].append(prop_name)

    ordered_sections: List[Tuple[str, List[str]]] = []
    for section, _ in SECTION_RULES:
        if buckets[section]:
            ordered_sections.append((section, buckets[section]))
    if buckets["Other"]:
        ordered_sections.append(("Other", buckets["Other"]))
    return ordered_sections


def _mark_dirty(flag_key: str):
    st.session_state[flag_key] = True


def _value_changed(current_value: Any, new_value: Any, dirty: bool = False) -> bool:
    if current_value is None:
        empty_like = (None, "", [], (), {})
        if new_value in empty_like:
            return False
        return dirty
    return current_value != new_value


def _current_value(task: Dict[str, Any], prop_name: str) -> Any:
    props = task.get("properties") or {}
    if prop_name in props:
        return props[prop_name]
    return task.get(_slugify(prop_name))


def _update_cached_task(task: Dict[str, Any], prop_name: str, prop_info: Dict[str, Any], value: Any) -> None:
    slug = _slugify(prop_name)
    task[slug] = value
    props = task.setdefault("properties", {})
    props[prop_name] = value
    if prop_info.get("type") == "title":
        task["title"] = value
    if prop_name in NOTES_PROPERTY_NAMES:
        task["notes"] = value



def render_dynamic_editor(notion: NotionHelper, selected_task: Dict[str, Any], autosave: bool, on_change_log):
    schema = notion.schema()
    sections = _build_sections(schema)

    st.sidebar.subheader("Editor")
    st.sidebar.caption("Edit fields below. Use Auto-Save to apply instantly.")

    if not sections:
        st.sidebar.info("No editable properties found in this database.")
        return False

    properties_map = selected_task.setdefault("properties", {})
    original = selected_task.copy()
    original["properties"] = dict(properties_map)

    pending_updates: Dict[str, Any] = {}
    save_banner = st.sidebar.empty()

    for section, fields in sections:
        with st.sidebar.expander(section, expanded=True):
            for prop_name in fields:
                prop_info = schema[prop_name]
                ptype = prop_info.get("type")
                key = f"{selected_task['id']}_{prop_name}"
                dirty_key = f"{key}__dirty"
                if dirty_key not in st.session_state:
                    st.session_state[dirty_key] = False

                current_value = _current_value(selected_task, prop_name)
                new_val: Any = current_value

                if ptype in ("select", "status"):
                    select_meta = prop_info.get(ptype, {}) or {}
                    options_data = select_meta.get("options", [])
                    options = [opt.get("name") for opt in options_data if opt.get("name")]
                    colors = {
                        opt.get("name"): COLOR_MAP.get((opt.get("color") or "default"), "#ddd")
                        for opt in options_data
                        if opt.get("name")
                    }
                    display_options = [NONE_OPTION] + options
                    try:
                        default_index = display_options.index(current_value)
                    except ValueError:
                        default_index = 0
                    choice = st.sidebar.selectbox(
                        prop_name,
                        display_options,
                        index=default_index,
                        key=key,
                        on_change=_mark_dirty,
                        args=(dirty_key,),
                    )
                    new_val = None if choice == NONE_OPTION else choice
                    if options:
                        st.sidebar.caption("Options:")
                        for opt in options:
                            _badge(opt, colors.get(opt))

                elif ptype == "multi_select":
                    options_data = prop_info.get("multi_select", {}) or {}
                    base_options = [
                        opt.get("name")
                        for opt in options_data.get("options", [])
                        if opt.get("name")
                    ]
                    current_list = current_value or []
                    display_options = list(dict.fromkeys(base_options + current_list))
                    new_val = st.sidebar.multiselect(
                        prop_name,
                        display_options,
                        default=current_list,
                        key=key,
                        on_change=_mark_dirty,
                        args=(dirty_key,),
                    )

                elif ptype == "date":
                    try:
                        dv = datetime.strptime(current_value, "%Y-%m-%d").date() if current_value else datetime.now().date()
                    except Exception:
                        dv = datetime.now().date()
                    date_val = st.sidebar.date_input(
                        prop_name,
                        value=dv,
                        key=key,
                        on_change=_mark_dirty,
                        args=(dirty_key,),
                    )
                    new_val = str(date_val)

                elif ptype == "number":
                    if isinstance(current_value, (int, float)):
                        number_value = float(current_value)
                    else:
                        try:
                            number_value = float(current_value)
                        except (TypeError, ValueError):
                            number_value = 0.0
                    new_val = st.sidebar.number_input(
                        prop_name,
                        value=number_value,
                        key=key,
                        on_change=_mark_dirty,
                        args=(dirty_key,),
                    )

                elif ptype == "title":
                    new_val = st.sidebar.text_input(
                        prop_name,
                        value=current_value or "",
                        key=key,
                        on_change=_mark_dirty,
                        args=(dirty_key,),
                    )

                elif ptype in {"url", "email", "phone_number"}:
                    new_val = st.sidebar.text_input(
                        prop_name,
                        value=current_value or "",
                        key=key,
                        on_change=_mark_dirty,
                        args=(dirty_key,),
                    )

                else:
                    new_val = st.sidebar.text_area(
                        prop_name,
                        value=current_value or "",
                        key=key,
                        on_change=_mark_dirty,
                        args=(dirty_key,),
                    )

                dirty = st.session_state.get(dirty_key, False)
                changed = _value_changed(current_value, new_val, dirty)

                if autosave and changed:
                    notion.update_property(selected_task["id"], prop_name, new_val)
                    on_change_log(prop_name, current_value, new_val)
                    save_banner.info(f"Saved {prop_name} at {datetime.now().strftime('%H:%M:%S')}")
                    _update_cached_task(selected_task, prop_name, prop_info, new_val)
                else:
                    if changed:
                        pending_updates[prop_name] = new_val
                    else:
                        pending_updates.pop(prop_name, None)

                st.session_state[dirty_key] = False

    if not autosave and st.sidebar.button("dY'_ Save All Changes"):
        desired: Dict[str, Any] = {}
        original_props = original.get("properties", {})
        for prop, val in pending_updates.items():
            if original_props.get(prop) != val:
                desired[prop] = val

        if desired:
            for prop, val in desired.items():
                old_val = original_props.get(prop)
                notion.update_property(selected_task["id"], prop, val)
                on_change_log(prop, old_val, val)
                _update_cached_task(selected_task, prop, schema[prop], val)
            st.sidebar.success("Saved")
            return True

        st.sidebar.info("No changes to save")

    return False
