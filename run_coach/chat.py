from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv
from openai import OpenAI

from run_coach.activities import coach_context, format_inbox, format_workouts, load_activities, pending_fit_files
from run_coach.conversation import append_message, conversation_path, load_messages, recent_dialogue
from run_coach.fit_import import import_inbox
from run_coach.profile import (
    FIELD_RULES,
    PROFILE_UPDATE_RULES,
    ProfileUpdateError,
    commit_profile_change,
    format_changelog,
    format_proposal,
    load_changelog,
    load_profile,
    propose_profile_update,
)

Confirm = Callable[[str], bool]

SYSTEM_PROMPT = f"""
You are a careful endurance running coach in a terminal conversation.

Use only the supplied profile, activities, and conversation. Do not invent weather, effort, splits, or intent.
Distances are miles and paces are minutes:seconds per mile.
Discuss the latest workouts. A single unusual run is not a fitness change.
If the athlete mentions pain, stay conservative, do not diagnose, and suggest rest or easy movement plus professional care when the pain is concerning.
Do not prescribe makeup workouts or extra hard training to compensate.
{PROFILE_UPDATE_RULES}
Ask one question at a time. Keep replies short.
""".strip()

PROPOSE_TOOL = {
    "type": "function",
    "name": "propose_profile_update",
    "description": (
        "Propose one allowed profile field change from a fact the athlete just stated. "
        "This does not save the profile."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "field": {"type": "string", "enum": sorted(FIELD_RULES)},
            "new_value": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "number"},
                    {"type": "boolean"},
                    {"type": "array", "items": {"type": "string"}},
                    {"type": "null"},
                ]
            },
            "reason": {"type": "string"},
        },
        "required": ["field", "new_value", "reason"],
        "additionalProperties": False,
    },
}


def greeting(profile: dict[str, Any]) -> str:
    name = profile.get("name")
    if isinstance(name, str) and name.strip():
        return f"Hi {name.strip()}. I'm your running coach."
    return "Hi. I'm your running coach."


def session_status(profile: dict[str, Any], activities: list[dict[str, Any]], pending: list[Path], prior_count: int) -> str:
    lines = [greeting(profile), format_inbox(pending), format_workouts(activities)]
    if prior_count:
        lines.append(f"Continuing a saved conversation ({prior_count} messages).")
    lines.append("Commands: /profile, /log, /workouts, /inbox, /import, /quit")
    return "\n".join(lines)


def local_command(line: str, *, profile: dict[str, Any], activities: list[dict[str, Any]], pending: list[Path], changelog: list[dict[str, Any]]) -> str | None:
    """Handle a slash command. Return None when the line is not a command."""
    if not line.startswith("/"):
        return None
    command = line.split()[0].lower()
    if command == "/profile":
        return json.dumps(profile, indent=2, ensure_ascii=False)
    if command == "/log":
        return format_changelog(changelog)
    if command == "/workouts":
        return format_workouts(activities)
    if command == "/inbox":
        return format_inbox(pending)
    if command == "/quit":
        return "quit"
    if command == "/import":
        return "import"
    return f"Unknown command: {command}"


def _parse_arguments(arguments: Any) -> dict[str, Any]:
    if isinstance(arguments, str):
        parsed = json.loads(arguments)
    elif isinstance(arguments, dict):
        parsed = arguments
    else:
        raise ProfileUpdateError("Tool arguments must be a JSON object.")
    if not isinstance(parsed, dict):
        raise ProfileUpdateError("Tool arguments must be a JSON object.")
    return parsed


def _tool_call(item: Any) -> tuple[str, str, dict[str, Any]] | None:
    item_type = item.get("type") if isinstance(item, dict) else getattr(item, "type", None)
    if item_type != "function_call":
        return None
    name = item.get("name") if isinstance(item, dict) else item.name
    call_id = item.get("call_id") if isinstance(item, dict) else item.call_id
    arguments = item.get("arguments") if isinstance(item, dict) else item.arguments
    return str(name), str(call_id), _parse_arguments(arguments)


def _as_input_item(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    if hasattr(item, "model_dump"):
        return item.model_dump(exclude_none=True)
    call = _tool_call(item)
    if call is None:
        raise TypeError("Unsupported model output item.")
    name, call_id, arguments = call
    return {"type": "function_call", "name": name, "call_id": call_id, "arguments": json.dumps(arguments)}


def resolve_profile_tool(
    profile: dict[str, Any],
    arguments: dict[str, Any],
    *,
    ask_confirm: Confirm,
    profile_path: Path,
    changelog_path: Path,
    client: Any | None = None,
) -> str:
    try:
        proposal = propose_profile_update(
            profile,
            str(arguments.get("field", "")),
            arguments.get("new_value"),
            str(arguments.get("reason", "")),
        )
    except ProfileUpdateError as exc:
        return f"Rejected: {exc}"
    print(format_proposal(proposal))
    accepted = ask_confirm("Apply this profile change? [y/n] ")
    entry = commit_profile_change(
        profile,
        proposal,
        accepted=accepted,
        profile_path=profile_path,
        changelog_path=changelog_path,
        client=client,
    )
    if accepted:
        return f"The athlete confirmed. Applied {entry['field']}."
    return f"The athlete declined. {entry['field']} was not changed."


def _response_text(response: Any) -> str:
    text = getattr(response, "output_text", "") or ""
    return text.strip()


def complete_exchange(
    *,
    client: Any,
    model: str,
    profile: dict[str, Any],
    activities: list[dict[str, Any]],
    pending: list[Path],
    messages: list[dict[str, Any]],
    user_message: str | None,
    ask_confirm: Confirm,
    profile_path: Path,
    changelog_path: Path,
    conversation_file: Path,
    store_client: Any | None = None,
) -> str:
    """Send one athlete turn, or an opening prompt when user_message is None."""
    model_input: list[dict[str, Any]] = list(recent_dialogue(messages))
    if user_message is not None:
        append_message(
            conversation_file,
            role="user",
            content=user_message,
            profile=profile,
            client=store_client,
        )
        messages.append({"role": "user", "content": user_message})
        model_input.append({"role": "user", "content": user_message})
    else:
        model_input.append(
            {
                "role": "user",
                "content": (
                    "The athlete just opened the session. Ask one specific question about the latest workout "
                    "if one is in the context. Otherwise ask whether they want to import a FIT file or describe a run. "
                    "Do not propose a profile update yet."
                ),
            }
        )

    reply_parts: list[str] = []
    for _ in range(4):
        instructions = SYSTEM_PROMPT + "\n\nCurrent context:\n" + json.dumps(
            coach_context(profile, activities, pending),
            indent=2,
            ensure_ascii=False,
        )
        response = client.responses.create(
            model=model,
            instructions=instructions,
            input=model_input,
            tools=[PROPOSE_TOOL],
            store=False,
        )
        text = _response_text(response)
        if text:
            reply_parts.append(text)
        output = getattr(response, "output", []) or []
        calls = [call for item in output if (call := _tool_call(item)) is not None]
        if not calls:
            break
        for item in output:
            model_input.append(_as_input_item(item))
        for name, call_id, arguments in calls:
            if name != "propose_profile_update":
                result = f"Unknown tool: {name}"
            else:
                result = resolve_profile_tool(
                    profile,
                    arguments,
                    ask_confirm=ask_confirm,
                    profile_path=profile_path,
                    changelog_path=changelog_path,
                    client=store_client,
                )
            model_input.append({"type": "function_call_output", "call_id": call_id, "output": result})
    reply = "\n\n".join(part for part in reply_parts if part).strip()
    if reply:
        append_message(
            conversation_file,
            role="assistant",
            content=reply,
            profile=profile,
            client=store_client,
        )
        messages.append({"role": "assistant", "content": reply})
    return reply


def ask_yes_no(prompt: str) -> bool:
    answer = input(prompt).strip().lower()
    return answer in {"y", "yes"}


def _record_status(
    conversation_file: Path,
    profile: dict[str, Any],
    text: str,
    store_client: Any | None,
) -> None:
    append_message(conversation_file, role="assistant", content=text, profile=profile, client=store_client)


def main() -> None:
    parser = argparse.ArgumentParser(description="Talk with the running coach about recent workouts.")
    parser.add_argument("--profile", default="data/athlete_profile.json", help="Athlete profile JSON.")
    parser.add_argument("--changelog", default="data/profile_changelog.jsonl", help="Profile change log.")
    parser.add_argument("--conversation-dir", default="data/conversations", help="Directory for saved conversations.")
    parser.add_argument("--activities", default="data/activities", help="Directory of normalized activities.")
    parser.add_argument("--fit-inbox", default="data/FIT_files", help="Directory of FIT files not yet imported.")
    parser.add_argument("--log", action="store_true", help="Print the profile change log and exit.")
    parser.add_argument("--show-profile", action="store_true", help="Print the athlete profile and exit.")
    args = parser.parse_args()

    load_dotenv()
    profile_path = Path(args.profile)
    changelog_path = Path(args.changelog)
    profile = load_profile(profile_path)
    if args.show_profile:
        print(json.dumps(profile, indent=2, ensure_ascii=False))
        return
    if args.log:
        print(format_changelog(load_changelog(changelog_path)))
        return

    activities_dir = Path(args.activities)
    inbox = Path(args.fit_inbox)
    conversation_file = conversation_path(args.conversation_dir, str(profile["athlete_id"]))
    messages = load_messages(conversation_file)
    pending = pending_fit_files(inbox)
    activities = load_activities(activities_dir)
    status = session_status(profile, activities, pending, len(messages))
    print(status)
    _record_status(conversation_file, profile, status, None)
    messages.append({"role": "assistant", "content": status})

    if pending and ask_yes_no("Import the new FIT files now? [y/n] "):
        import_inbox(inbox=inbox, archive_dir=inbox / "old_files", output_dir=activities_dir)
        pending = pending_fit_files(inbox)
        activities = load_activities(activities_dir)
        print(format_inbox(pending))
        print(format_workouts(activities))

    if not os.getenv("OPENAI_API_KEY"):
        print("Set OPENAI_API_KEY in .env to talk through the workouts. /profile, /log, /workouts, and /import still work.")
        client = None
    else:
        client = OpenAI()
        model = os.getenv("OPENAI_MODEL", "gpt-5-mini")
        opening = complete_exchange(
            client=client,
            model=model,
            profile=profile,
            activities=activities,
            pending=pending,
            messages=messages,
            user_message=None,
            ask_confirm=ask_yes_no,
            profile_path=profile_path,
            changelog_path=changelog_path,
            conversation_file=conversation_file,
        )
        if opening:
            print(f"\nCoach: {opening}")

    while True:
        try:
            line = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        command = local_command(
            line,
            profile=profile,
            activities=activities,
            pending=pending,
            changelog=load_changelog(changelog_path),
        )
        if command == "quit":
            return
        if command == "import":
            import_inbox(inbox=inbox, archive_dir=inbox / "old_files", output_dir=activities_dir)
            pending = pending_fit_files(inbox)
            activities = load_activities(activities_dir)
            print(format_inbox(pending))
            print(format_workouts(activities))
            continue
        if command is not None:
            print(command)
            continue
        if client is None:
            print("Set OPENAI_API_KEY in .env to talk to the coach.")
            continue
        reply = complete_exchange(
            client=client,
            model=os.getenv("OPENAI_MODEL", "gpt-5-mini"),
            profile=profile,
            activities=activities,
            pending=pending,
            messages=messages,
            user_message=line,
            ask_confirm=ask_yes_no,
            profile_path=profile_path,
            changelog_path=changelog_path,
            conversation_file=conversation_file,
        )
        if reply:
            print(f"\nCoach: {reply}")


if __name__ == "__main__":
    main()
