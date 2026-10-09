from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def conversation_path(directory: str | Path, athlete_id: str) -> Path:
    safe = "".join(character for character in athlete_id if character.isalnum() or character in "-_") or "athlete"
    return Path(directory) / f"{safe}.jsonl"


def load_messages(path: str | Path) -> list[dict[str, Any]]:
    file = Path(path)
    if not file.exists():
        return []
    messages: list[dict[str, Any]] = []
    for line in file.read_text(encoding="utf-8").splitlines():
        if line.strip():
            messages.append(json.loads(line))
    return messages


def append_message(
    path: str | Path,
    *,
    role: str,
    content: str,
    profile: dict[str, Any] | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    message = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "role": role,
        "content": content,
    }
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(message, ensure_ascii=False) + "\n")
    if profile is not None:
        _mirror_message(profile, message, client)
    return message


def recent_dialogue(messages: list[dict[str, Any]], *, limit: int = 20) -> list[dict[str, str]]:
    """Plain user and assistant turns for the next model call. No retrieval ranking."""
    dialogue = [
        {"role": message["role"], "content": message["content"]}
        for message in messages
        if message.get("role") in {"user", "assistant"} and message.get("content")
    ]
    return dialogue[-limit:]


def _mirror_message(profile: dict[str, Any], message: dict[str, Any], client: Any | None) -> None:
    from run_coach.store import save_conversation_message, supabase_configured

    if client is None and not supabase_configured():
        return
    save_conversation_message(
        profile=profile,
        role=message["role"],
        content=message["content"],
        created_at=message["created_at"],
        client=client,
    )
