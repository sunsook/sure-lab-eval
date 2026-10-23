"""Record explicit human decisions from a UserPromptSubmit hook."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from golden_lib import (
    case_hash, case_state, load_decisions, load_golden, load_intents, load_runs,
    in_run_scope, require_yaml,
)


GS = r"GS-\d{3}"
ID_LIST = rf"{GS}(?:,{GS})*"
APPROVAL_HEADER = re.compile(r"^\[승인 요청 (B-\d{3})\]$")
APPROVAL_LINE = re.compile(r"^(\d+)\. (GS-\d{3}) · 지문 ([0-9a-f]{8}) — (.+)$")
SCOPE_HELP = "범위(GS-xxx / 하네스 / CORE)를 넣어 실행 <범위> 를 입력하세요"


class GateError(Exception):
    """A recognized decision cannot be safely recorded."""


def _emit(data: dict) -> None:
    sys.stdout.buffer.write((json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8"))


def _block(reason: str) -> None:
    message = f"{reason} — 고친 뒤 같은 결정 문법으로 다시 입력하세요"
    _emit({"decision": "block", "reason": message, "systemMessage": message})


def _success(message: str, next_action: str) -> None:
    _emit({"systemMessage": message, "hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": f"{message}\n{next_action}",
    }})


def _parse_prompt(prompt: str):
    first = prompt.strip().splitlines()[0].strip() if prompt.strip() else ""
    approve = re.match(rf"^승인 (모두|\d+(?:,\d+)*|{ID_LIST})(?=$|\s)", first)
    if approve:
        return "APPROVE", approve.group(1)
    if first == "실행":
        return "RUN", ""
    run = re.fullmatch(r"실행 ([^\s]+)(?: ([1-9]\d*회))?(?: (ablate))?", first)
    if run:
        scope = run.group(1)
        if re.fullmatch(ID_LIST, scope) or scope == "CORE" or re.search(r"[/\\.:]", scope):
            return "RUN", first[len("실행 "):]
    for word, verb in (("동의", "AGREE"), ("비동의", "DISAGREE"), ("판정", "VERDICT"),
                       ("골든셋오류", "GS_ERROR"), ("폐기", "RETIRE")):
        match = re.match(rf"^{word} ({GS})(?=$|\s)(.*)$", first)
        if match:
            return verb, (match.group(1), match.group(2).strip())
    return None, None


def _last_assistant_text(path: Path) -> str:
    last = ""
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if row.get("type") != "assistant" or row.get("isSidechain"):
                continue
            message = row.get("message") or {}
            if message.get("isSidechain"):
                continue
            content = message.get("content") or []
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            parts = [part.get("text", "") for part in content
                     if isinstance(part, dict) and part.get("type") == "text" and part.get("text")]
            if parts:
                last = "\n".join(parts)
    return last


def _request_lines(text: str) -> list[tuple[int, str, str]]:
    lines = text.splitlines()
    headers = [n for n, line in enumerate(lines) if APPROVAL_HEADER.fullmatch(line.strip())]
    if not headers:
        raise GateError("블록 없음: AI 의 승인 요청에 답하는 형식으로만 승인하세요")
    start = headers[-1] + 1
    result = []
    for line in lines[start:]:
        match = APPROVAL_LINE.fullmatch(line.strip())
        if not match:
            break
        result.append((int(match.group(1)), match.group(2), match.group(3)))
    if not result or len(result) > 4 or [number for number, _, _ in result] != list(range(1, len(result) + 1)):
        raise GateError("승인 요청 블록은 1~4개의 연속 번호가 필요합니다")
    if len({gs_id for _, gs_id, _ in result}) != len(result):
        raise GateError("승인 요청 블록에 중복 GS ID가 있습니다")
    return result


def _approvals(root: Path, selection: str, cases: dict, decisions: list[dict], transcript_path: Path):
    lines = _request_lines(_last_assistant_text(transcript_path))
    if selection == "모두":
        chosen = lines
    elif selection.startswith("GS-"):
        ids = selection.split(",")
        if len(ids) != len(set(ids)):
            raise GateError("중복 승인 대상")
        by_id = {gs_id: line for line in lines for gs_id in [line[1]]}
        if any(gs_id not in by_id for gs_id in ids):
            raise GateError("승인 요청 블록 밖 GS ID입니다")
        chosen = [by_id[gs_id] for gs_id in ids]
    else:
        numbers = [int(value) for value in selection.split(",")]
        if len(numbers) != len(set(numbers)) or any(number < 1 or number > len(lines) for number in numbers):
            raise GateError("승인 요청 블록 밖 번호입니다")
        chosen = [lines[number - 1] for number in numbers]
    rows = []
    for _, gs_id, fingerprint in chosen:
        case = cases.get(gs_id)
        if case is None:
            raise GateError(f"없는 사례 {gs_id}")
        digest = case_hash(case, root)
        if digest[:8] != fingerprint:
            raise GateError(f"{gs_id} 지문 불일치: 요청 블록을 다시 제시하세요")
        state = case_state(gs_id, digest, decisions)
        if state not in ("APPROVAL_MISSING", "APPROVAL_STALE"):
            raise GateError(f"{gs_id} 승인 불가: {state}")
        rows.append({"verb": "APPROVE", "gs_id": gs_id, "content_sha256": digest})
    return rows, "승인 " + ", ".join(f"{row['gs_id']} 지문 {row['content_sha256'][:8]} → APPROVED" for row in rows)


def _run(root: Path, value: str, cases: dict, decisions: list[dict]):
    if not value:
        raise GateError(SCOPE_HELP)
    tokens = value.split()
    ablate = False
    if tokens and tokens[-1].lower() == "ablate":
        ablate = True
        tokens.pop()
    runs = 1
    if tokens and re.fullmatch(r"[1-9]\d*회", tokens[-1]):
        runs = int(tokens.pop()[:-1])
    if len(tokens) != 1:
        raise GateError(SCOPE_HELP)
    scope_text = tokens[0]
    if re.fullmatch(ID_LIST, scope_text):
        ids = scope_text.split(",")
        if len(ids) != len(set(ids)) or any(gs_id not in cases for gs_id in ids):
            raise GateError("없는 사례 또는 중복 GS ID입니다")
        scope = {"cases": ids}
    elif scope_text == "CORE":
        scope = {"priority": "CORE"}
    else:
        intents = load_intents(root / "eval" / "intent.md")
        targets = {target for intent in intents.values() for target in (intent.get("target") or [])}
        if scope_text not in targets:
            raise GateError("없는 하네스 범위입니다: " + SCOPE_HELP)
        scope = {"target": scope_text}
    intents = load_intents(root / "eval" / "intent.md")
    states = {}
    for gs_id, case in cases.items():
        intent_id = case["intent_id"]
        if intent_id not in intents:
            raise GateError(f"{gs_id}: 없는 Intent {intent_id}")
        if in_run_scope(case, intents[intent_id], scope):
            states[gs_id] = case_state(gs_id, case_hash(case, root), decisions)
    retry = "승인 뒤 실행 <범위> 를 다시 입력하세요"
    if "cases" in scope:
        rejected = [f"{gs_id} {state}" for gs_id, state in states.items() if state != "APPROVED"]
        if rejected:
            raise GateError(" · ".join(rejected) + "; " + retry)
    elif "APPROVED" not in states.values():
        counts = {state: list(states.values()).count(state) for state in sorted(set(states.values()))}
        detail = " · ".join(f"{state}={count}" for state, count in counts.items())
        raise GateError(f"범위에 승인된 사례가 없습니다; {detail}; {retry}")
    return {"verb": "RUN", "scope": scope, "value": {"runs": runs, "ablate": ablate}}


def _case_decision(root: Path, verb: str, gs_id: str, detail: str, cases: dict, decisions: list[dict]):
    case = cases.get(gs_id)
    if case is None:
        raise GateError(f"없는 사례 {gs_id}")
    digest = case_hash(case, root)
    state = case_state(gs_id, digest, decisions)
    if state == "RETIRED":
        raise GateError(f"{gs_id} RETIRED: 이후 결정 불가")
    row = {"verb": verb, "gs_id": gs_id, "content_sha256": digest}
    if verb == "RETIRE":
        if detail:
            raise GateError("폐기 문법: 폐기 GS-xxx")
        return row
    if verb == "VERDICT":
        if detail not in ("PASS", "FAIL"):
            raise GateError(f"판정 {gs_id} PASS|FAIL 형식으로 입력하세요")
        row["value"] = detail
    elif verb == "GS_ERROR":
        parts = detail.split(maxsplit=1)
        if not parts or parts[0] not in ("NOT_HARNESS", "OFF_INTENT", "UNJUDGEABLE"):
            raise GateError(f"골든셋오류 {gs_id} NOT_HARNESS|OFF_INTENT|UNJUDGEABLE [사유]")
        row["value"] = parts[0]
        if len(parts) > 1:
            row["reason"] = parts[1]
    elif detail:
        raise GateError(f"{verb} 문법에 불필요한 값이 있습니다")
    runs = load_runs(root / "eval")
    matching = [(run, result) for run in runs for result in run.get("cases", [])
                if result.get("id") == gs_id and result.get("condition") == "WITH"]
    if not matching:
        raise GateError(f"{gs_id} 실행 기록이 없습니다")
    run, result = matching[-1]
    if result.get("content_sha256") != digest:
        raise GateError(f"{gs_id} 최신 run 지문 불일치")
    verdict = result.get("verdict")
    if verb in ("AGREE", "DISAGREE") and verdict not in ("PASS", "FAIL"):
        raise GateError(f"판정 {gs_id} PASS|FAIL 형식으로 입력하세요")
    if verb == "VERDICT" and verdict not in ("REVIEW", "ERROR"):
        raise GateError(f"{gs_id} 판정은 REVIEW 또는 ERROR에서만 가능합니다")
    row["run_id"] = run["run_id"]
    return row


def _approver(root: Path) -> str:
    result = subprocess.run(["git", "config", "user.name"], cwd=root,
                            encoding="utf-8", capture_output=True, check=False)
    name = result.stdout.strip() if result.returncode == 0 else ""
    if not name:
        raise GateError('git config --global user.name "<GitHub 이름>" 을 설정하세요')
    return name


def _record(path: Path, existing: list[dict], rows: list[dict]) -> None:
    yaml = require_yaml()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        content = path.read_text(encoding="utf-8")
    else:
        content = "schema_version: 1\ndecisions:\n"
    if not existing and re.search(r"(?m)^decisions: \[\]\s*$", content):
        content = re.sub(r"(?m)^decisions: \[\]\s*$", "decisions:\n", content)
    addition = yaml.safe_dump(rows, allow_unicode=True, sort_keys=False)
    candidate = content.rstrip() + "\n" + addition
    data = yaml.safe_load(candidate)
    if data.get("schema_version") != 1 or not isinstance(data.get("decisions"), list) or len(data["decisions"]) != len(existing) + len(rows):
        raise GateError("결정 대장 저장 검증 실패")
    temporary = path.with_suffix(".yaml.tmp")
    temporary.write_text(candidate, encoding="utf-8")
    try:
        temporary.replace(path)
        if len(load_decisions(path)) != len(existing) + len(rows):
            raise GateError("결정 대장 재읽기 검증 실패")
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        prompt = payload.get("prompt", "")
        verb, detail = _parse_prompt(prompt)
        if verb is None:
            return 0
        root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload["cwd"]).resolve()
        eval_dir = root / "eval"
        require_yaml()
        decisions = load_decisions(eval_dir / "decisions.yaml")
        golden = load_golden(eval_dir / "golden-set.yaml")
        cases = {case["id"]: case for case in golden["cases"]}
        approver = _approver(root)
        if verb == "APPROVE":
            rows, summary = _approvals(root, detail, cases, decisions, Path(payload["transcript_path"]))
        elif verb == "RUN":
            rows, summary = [_run(root, detail, cases, decisions)], f"실행 요청 {detail} 기록"
        else:
            gs_id, extra = detail
            rows = [_case_decision(root, verb, gs_id, extra, cases, decisions)]
            summary = ""
        next_seq = decisions[-1]["seq"] + 1 if decisions else 1
        stamp = datetime.now(timezone.utc).isoformat()
        for row in rows:
            row.update({"seq": next_seq, "at": stamp, "approver": approver,
                        "prompt_excerpt": prompt[:200]})
            next_seq += 1
        if verb not in ("APPROVE", "RUN"):
            state = case_state(gs_id, rows[0]["content_sha256"], decisions + rows)
            summary = f"{verb} {gs_id} 지문 {rows[0]['content_sha256'][:8]} → {state} 기록"
        _record(eval_dir / "decisions.yaml", decisions, rows)
        if verb == "RUN":
            summary += f"; python {Path(__file__).parent / 'run_eval.py'} --request {rows[0]['seq']} --dry-run 으로 예상 호출 수를 먼저 보이고 실행하세요"
        _success(summary, "기록된 결정만 적용하고 다음 절차를 진행하세요")
    except Exception as exc:
        _block(f"{type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
