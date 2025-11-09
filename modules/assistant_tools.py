from typing import List, Dict, Any
from anthropic import Anthropic
from datetime import datetime

class AssistantOps:
    def __init__(self, anthropic: Anthropic):
        self.anthropic = anthropic
        self.model = "claude-sonnet-4-5-20250929"

    def summarize_completion(self, task_title: str, notes: str) -> str:
        prompt = (
            f"Task completed: {task_title}\n"
            f"Notes or context: {notes}\n\n"
            "Write a concise 2 to 3 sentence summary of what was accomplished, practical and specific."
        )
        msg = self.anthropic.messages.create(
            model=self.model,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        # Extract text blocks
        out = []
        for b in msg.content:
            if hasattr(b, "text"):
                out.append(b.text)
        return "\n".join(out).strip()

    def weekly_report(self, completed: List[Dict[str, Any]]) -> str:
        items = []
        for t in completed:
            line = f"- {t.get('title')} [Done, due {t.get('due_date') or 'n/a'}]"
            items.append(line)
        body = "\n".join(items) if items else "- No completed items in the period"
        prompt = (
            "Create a clear weekly progress report for a product owner.\n"
            "Keep it under 250 words. Use bullet points.\n\n"
            f"Completed items last week:\n{body}"
        )
        msg = self.anthropic.messages.create(
            model=self.model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        out = []
        for b in msg.content:
            if hasattr(b, "text"):
                out.append(b.text)
        return "\n".join(out).strip()
