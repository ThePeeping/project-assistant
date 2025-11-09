from typing import Optional, Dict, Any
from datetime import datetime

class EventLogger:
    """Thin wrapper that logs to memory, and to Supabase if available."""
    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self.buffer = []

    def log(self, event: str, user_id: str, meta: Optional[Dict[str, Any]] = None):
        record = {
            "timestamp": datetime.utcnow().isoformat(),
            "event": event,
            "user_id": user_id,
            "meta": meta or {},
        }
        self.buffer.append(record)
        # Optional Supabase table: project_logs with columns: timestamp, event, user_id, meta (json)
        try:
            if self.supabase is not None:
                self.supabase.table("project_logs").insert(record).execute()
        except Exception as e:
            # Do not crash the UI for logging issues
            self.buffer.append({"error": str(e)})

    def flush(self):
        self.buffer.clear()
