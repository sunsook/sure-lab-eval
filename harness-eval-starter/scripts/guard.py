"""PreToolUse guard for the starter's human decisions and hook machinery."""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path


GUIDANCE = ("사람 결정은 사람이 프롬프트로 입력한다 — 승인은 AI 의 [승인 요청] 에 "
            "답해서(승인 모두 / 1,2,4 / GS-xxx)")
PROTECTED_SCRIPTS = {"human_gate.py", "guard.py", "golden_lib.py", "run_eval.py"}
READ_COMMAND = re.compile(r"^\s*(?:git\s+(?:add|commit|diff|log|status|show)|cat|type|Get-Content)\b", re.I)
WRITER = re.compile(
    r">|\b(?:tee|cp|mv|rm|del|Set-Content|Out-File|Add-Content|Remove-Item|Move-Item|Copy-Item)\b|\bsed\s+-i\b",
    re.I,
)
PYTHON = re.compile(r"\b(?:python(?:\.exe)?|py(?:\.exe)?)\b", re.I)
STARTER_HOOK = re.compile(r"(?:human_gate|guard)\.py\b", re.I)


def _normal(path: str) -> str:
    return path.replace("\\", "/").lower()


def _settings(path: str) -> bool:
    return bool(re.search(r"(?:^|/)\.claude/settings(?:\.local)?\.json$", _normal(path)))


def _protected(path: str) -> bool:
    path = _normal(path)
    if re.search(r"(?:^|/)eval/decisions\.yaml(?:$|[/'\"\s])", path):
        return True
    if re.search(r"(?:^|/)eval/runs(?:/|$)", path):
        return True
    marker = "harness-eval-starter/scripts/"
    return any(re.search(rf"(?:^|/){re.escape(marker)}{name}(?:$|[/'\"\s])", path)
               for name in PROTECTED_SCRIPTS)


def _hook_entries(source: str) -> Counter:
    data = json.loads(source)
    if not isinstance(data, dict):
        raise ValueError("settings JSON object expected")
    found = Counter()
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("settings hooks object expected")
    for event, groups in hooks.items():
        for group in groups:
            for entry in group.get("hooks", []):
                if STARTER_HOOK.search(str(entry.get("command", ""))):
                    key = json.dumps({"event": event, "matcher": group.get("matcher"), "entry": entry},
                                     ensure_ascii=False, sort_keys=True)
                    found[key] += 1
    return found


def _settings_changed(path: str, tool: str, inputs: dict, cwd: str) -> bool:
    file = Path(path)
    if not file.is_absolute():
        file = Path(cwd) / file
    before = file.read_text(encoding="utf-8") if file.exists() else "{}"
    if tool == "Write":
        after = inputs["content"]
    else:
        old = inputs["old_string"]
        new = inputs["new_string"]
        if not old or old not in before:
            raise ValueError("settings edit target not found")
        after = before.replace(old, new) if inputs.get("replace_all") else before.replace(old, new, 1)
    original = _hook_entries(before)
    updated = _hook_entries(after)
    return any(updated[key] < count for key, count in original.items())


def _command_path(text: str) -> bool:
    normalized = _normal(text)
    if re.search(r"(?:^|[^\w])eval/decisions\.yaml\b|(?:^|[^\w])eval/runs(?:/|\b)", normalized):
        return True
    return bool(re.search(r"(?:^|/)harness-eval-starter/scripts/(?:human_gate|guard|golden_lib|run_eval)\.py\b",
                          normalized))


def _human_gate_run(command: str) -> bool:
    # An interpreter command may be preceded by input through a pipe.
    normalized = _normal(command)
    return bool(re.search(r"\b(?:python(?:\.exe)?|py(?:\.exe)?)\b[^\n|;&]*human_gate\.py\b",
                          normalized) or re.search(r"(?:^|[|;&\n]\s*)(?:\./)?\S*/human_gate\.py\b", normalized))


def _command_blocked(command: str) -> bool:
    if _human_gate_run(command):
        return True
    if READ_COMMAND.match(command) and not re.search(r"[|;&>\n]", command):
        return False
    if not _command_path(command):
        return False
    if WRITER.search(command):
        return True
    if PYTHON.search(command):
        # Running one of the other starter scripts is allowed. Any other Python
        # command containing a protected path can use that path as a write target.
        if re.fullmatch(r"\s*(?:python(?:\.exe)?|py(?:\.exe)?)\s+\S*/(?:run_eval|validate_golden|eval_summary)\.py(?:\s+[^\n;&|>]*)?\s*",
                        _normal(command)):
            return False
        return True
    return False


def _block_reason(payload: dict) -> str | None:
    tool = payload["tool_name"]
    inputs = payload["tool_input"]
    if not isinstance(inputs, dict):
        raise ValueError("tool_input object expected")
    if tool in ("Edit", "Write", "NotebookEdit"):
        path = inputs.get("file_path") or inputs.get("notebook_path")
        if not isinstance(path, str):
            raise ValueError("file_path or notebook_path expected")
        if _settings(path):
            try:
                if _settings_changed(path, tool, inputs, payload["cwd"]):
                    return f"starter 훅 항목 변경: {path}"
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                return f"settings 훅 항목 비교 실패: {path}: {exc}"
        elif _protected(path):
            return f"보호 파일 편집: {path}"
    elif tool in ("Bash", "PowerShell"):
        command = inputs.get("command")
        if not isinstance(command, str):
            raise ValueError("command string expected")
        if _command_blocked(command):
            return f"보호 대상 명령: {command}"
    else:
        raise ValueError(f"unknown tool_name: {tool}")
    return None


def main() -> int:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("stdin JSON object expected")
        reason = _block_reason(payload)
    except (ValueError, KeyError, TypeError, UnicodeError) as exc:
        sys.stderr.buffer.write((f"guard 입력 판독 실패: {exc}\n").encode("utf-8"))
        return 0
    if reason:
        sys.stderr.buffer.write((f"{GUIDANCE}\n{reason}\n").encode("utf-8"))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
