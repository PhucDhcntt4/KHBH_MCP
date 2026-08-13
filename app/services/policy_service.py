from pathlib import Path
from typing import Any

from app.config import WARRANTY_POLICY_PATH


class PolicyService:
    def __init__(
        self,
        path: str | Path = WARRANTY_POLICY_PATH,
    ) -> None:
        self.path = Path(path)

    def search(self, question: str) -> dict[str, Any]:
        if not question.strip():
            return {
                "success": False,
                "status": "invalid_question",
                "content": "",
            }

        if not self.path.exists():
            return {
                "success": False,
                "status": "policy_not_found",
                "content": "",
            }

        content = self.path.read_text(
            encoding="utf-8"
        ).strip()

        if not content:
            return {
                "success": False,
                "status": "policy_empty",
                "content": "",
            }

        return {
            "success": True,
            "status": "policy_found",
            "source": self.path.name,
            "content": content,
        }
