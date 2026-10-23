"""Validate a target project's golden set and show read-only draft views."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from golden_lib import (
    FixtureMissing, SchemaError, YamlMissing, case_hash, case_state,
    load_decisions, load_golden, load_intents, pick_draft_targets,
    require_yaml, status_counts,
)


def _say(message: str, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    stream.buffer.write((message + "\n").encode("utf-8"))


def _schema() -> dict:
    content = (Path(__file__).resolve().parent.parent / "references" / "schema.md").read_text(encoding="utf-8")
    match = re.search(r"```yaml schema\n(.*?)\n```", content, re.DOTALL)
    if match is None:
        raise ValueError("schema.md의 yaml schema 블록이 없습니다")
    return require_yaml().safe_load(match.group(1))


def _settings_commands(root: Path, event: str, script_name: str) -> list[str]:
    path = root / ".claude" / "settings.json"
    settings = json.loads(path.read_text(encoding="utf-8"))
    entries = settings.get("hooks", {}).get(event, [])
    commands = []
    for entry in entries:
        for hook in entry.get("hooks", []):
            command = hook.get("command", "")
            if re.search(r"(?<![\w.])" + re.escape(script_name) + r"(?![\w.])", command):
                commands.append(command)
    return commands


def _rule_present(root: Path, case: dict, intent: dict) -> bool:
    text = case.get("rule_text")
    source = case.get("rule_source")
    if not isinstance(text, str) or not text or not isinstance(source, str):
        return False
    targets = intent.get("target") or []
    for target in targets:
        if not isinstance(target, str):
            continue
        if target.startswith("settings:"):
            parts = target.split(":", 2)
            if len(parts) != 3:
                continue
            _, event, script_name = parts
            try:
                commands = _settings_commands(root, event, script_name)
            except (OSError, ValueError, TypeError):
                continue
            if any(text in command for command in commands):
                return True
            if commands:
                script_paths = [root / "scripts" / script_name, root / script_name]
                for command in commands:
                    for token in re.findall(r"[^\s\"']+" + re.escape(script_name) + r"(?=\s|$|[\"'])", command):
                        script_paths.append(root / token.replace("\\", "/"))
                for script_path in script_paths:
                    try:
                        if text in script_path.read_text(encoding="utf-8"):
                            return True
                    except OSError:
                        continue
        else:
            match = re.fullmatch(r"(.+):(\d+)", source)
            if match and Path(match.group(1)).as_posix() == Path(target).as_posix():
                try:
                    if text in (root / target).read_text(encoding="utf-8"):
                        return True
                except OSError:
                    continue
    return False


def validate(root: Path, intents: dict, golden: dict, schema: dict) -> list[str]:
    errors = []
    cases = golden["cases"]
    seen = set()
    batches = Counter()
    linked = {}
    for intent_id, intent in intents.items():
        missing = set(schema["intent_fields"]) - set(intent)
        if missing:
            errors.append(f"{intent_id}: Intent 필드 없음: {', '.join(sorted(missing))}")
        if not re.fullmatch(schema["intent_id_re"], intent_id):
            errors.append(f"{intent_id}: Intent ID 형식 오류")
        links = intent.get("golden_set_links", [])
        if not isinstance(links, list):
            errors.append(f"{intent_id}: golden_set_links 목록이 필요합니다")
        else:
            for gs_id in links:
                linked.setdefault(gs_id, set()).add(intent_id)
    for index, case in enumerate(cases, 1):
        if not isinstance(case, dict):
            errors.append(f"사례 {index}: mapping이 필요합니다")
            continue
        gs_id = case.get("id", f"사례 {index}")
        missing = set(schema["case_fields"]) - set(case)
        if missing:
            errors.append(f"{gs_id}: 사례 필드 없음: {', '.join(sorted(missing))}")
        if not isinstance(gs_id, str) or not re.fullmatch(schema["gs_id_re"], gs_id):
            errors.append(f"{gs_id}: GS ID 형식 오류")
        if gs_id in seen:
            errors.append(f"{gs_id}: 중복 ID")
        seen.add(gs_id)
        batch = case.get("batch_id")
        if not isinstance(batch, str) or not re.fullmatch(schema["batch_id_re"], batch):
            errors.append(f"{gs_id}: batch_id 형식 오류")
        else:
            batches[batch] += 1
        method = case.get("method")
        if method not in schema["enums"]["method"]:
            errors.append(f"{gs_id}: method 값 오류")
        input_data = case.get("input")
        required_input = {"SCRIPT": "command", "LLM_JUDGE": "prompt"}.get(method)
        if not isinstance(input_data, dict) or (required_input and not isinstance(input_data.get(required_input), str)) or (required_input and not input_data.get(required_input)):
            errors.append(f"{gs_id}: input.{required_input or '형식'} 필요")
        criteria = case.get("judgement_criteria")
        if method == "SCRIPT" and (not isinstance(criteria, dict) or
                                   any(key not in schema["script_criteria_keys"] for key in criteria) or
                                   not isinstance(criteria.get("exit_code"), int)):
            errors.append(f"{gs_id}: SCRIPT judgement_criteria에 exit_code가 필요합니다")
        if method == "LLM_JUDGE" and (not isinstance(criteria, str) or not criteria):
            errors.append(f"{gs_id}: LLM_JUDGE judgement_criteria 문자열이 필요합니다")
        for key in ("core_intent", "failure_situation", "expected_behavior", "observable_evidence", "rule_source", "rule_text"):
            if not isinstance(case.get(key), str) or not case.get(key):
                errors.append(f"{gs_id}: {key} 문자열이 필요합니다")
        intent_id = case.get("intent_id")
        intent = intents.get(intent_id)
        if intent is None:
            errors.append(f"{gs_id}: 없는 intent_id {intent_id}")
        else:
            if intent.get("work_type") != "HARNESS":
                errors.append(f"{gs_id}: {intent_id} 하네스 의도가 아님")
            if gs_id not in (intent.get("golden_set_links") or []):
                errors.append(f"{gs_id}: {intent_id} golden_set_links에 사례가 없습니다")
            if not _rule_present(root, case, intent):
                errors.append(f"{gs_id}: rule_text가 target에 원문으로 없습니다")
        if isinstance(input_data, dict) and input_data.get("fixture"):
            try:
                case_hash(case, root)
            except FixtureMissing as exc:
                errors.append(f"{gs_id}: fixture 파일 없음: {exc}")
    for batch, count in sorted(batches.items()):
        if count > schema["approval_max_lines"]:
            errors.append(f"배치 {batch} {count}건 > {schema['approval_max_lines']}")
    for gs_id, intent_ids in linked.items():
        for intent_id in intent_ids:
            if not any(case.get("id") == gs_id and case.get("intent_id") == intent_id
                       for case in cases if isinstance(case, dict)):
                errors.append(f"{gs_id}: {intent_id} golden_set_links ↔ 사례 intent_id 불일치")
    return errors


def request_block(root: Path, batch: str, golden: dict, decisions: list[dict]) -> list[str]:
    lines = []
    for case in golden["cases"]:
        if case.get("batch_id") != batch:
            continue
        digest = case_hash(case, root)
        state = case_state(case["id"], digest, decisions)
        if state in ("APPROVAL_MISSING", "APPROVAL_STALE"):
            lines.append(f"{len(lines) + 1}. {case['id']} · 지문 {digest[:8]} — {case['core_intent']}")
    return [f"[승인 요청 {batch}]", *lines] if lines else []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--draft-targets", action="store_true")
    mode.add_argument("--request-block")
    parser.add_argument("--target")
    args = parser.parse_args()
    try:
        schema = _schema()
        root = args.root.resolve()
        eval_dir = root / "eval"
        if args.status and not eval_dir.exists():
            for key in ("core_total", "core_with_gs", "core_without_gs", "recommended_not_created"):
                _say(f"{key}=0")
            return 0
        intents = load_intents(eval_dir / "intent.md")
        golden = load_golden(eval_dir / "golden-set.yaml")
        if args.status:
            decisions = load_decisions(eval_dir / "decisions.yaml")
            counts = status_counts(intents, golden, decisions, root, target=args.target)
            for key in ("core_total", "core_with_gs", "core_without_gs", "recommended_not_created"):
                _say(f"{key}={counts[key]}")
            for state, count in sorted(counts["states"].items()):
                _say(f"{state}={count}")
            return 0
        if args.draft_targets:
            for intent_id in pick_draft_targets(intents, golden, target=args.target):
                _say(intent_id)
            return 0
        errors = validate(root, intents, golden, schema)
        if errors:
            for error in errors:
                _say(error)
            return 1
        if args.request_block:
            if not re.fullmatch(schema["batch_id_re"], args.request_block):
                _say("batch_id 형식 오류")
                return 1
            decisions = load_decisions(eval_dir / "decisions.yaml")
            lines = request_block(root, args.request_block, golden, decisions)
            if not lines:
                _say("승인할 DRAFT 없음")
                return 1
            for line in lines:
                _say(line)
            return 0
        _say(f"OK {len(golden['cases'])} cases")
        return 0
    except YamlMissing:
        _say("PyYAML이 필요합니다: python -m pip install pyyaml", error=True)
        return 3
    except Exception as exc:
        _say(f"{type(exc).__name__}: {exc}", error=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
