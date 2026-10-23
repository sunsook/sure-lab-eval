"""Read starter eval data and compute content hashes and case lifecycle."""

from __future__ import annotations

import hashlib
import importlib
import json
import re
from datetime import date, datetime
from pathlib import Path


class YamlMissing(Exception):
    """PyYAML is required for starter data."""


class SchemaError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


class FixtureMissing(Exception):
    """A case refers to a fixture that is absent."""


def require_yaml():
    try:
        return importlib.import_module("yaml")
    except ImportError as exc:
        raise YamlMissing("PyYAML이 필요합니다: pip install pyyaml") from exc


def _read_yaml(path: Path):
    yaml = require_yaml()
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SchemaError([f"{path}: {exc}"]) from exc


def load_intents(path) -> dict[str, dict]:
    path = Path(path)
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SchemaError([f"{path}: {exc}"]) from exc
    yaml = require_yaml()
    headings = list(re.finditer(r"(?m)^### (INT-\d{3})\s*$", content))
    errors = []
    intents = {}
    if not headings:
        errors.append(f"{path}: Intent 제목이 없습니다")
    for index, heading in enumerate(headings):
        heading_id = heading.group(1)
        section = content[heading.end():headings[index + 1].start() if index + 1 < len(headings) else len(content)]
        blocks = re.findall(r"(?ms)^```yaml\s*\n(.*?)^```\s*$", section)
        if len(blocks) != 1:
            errors.append(f"{heading_id}: yaml 블록은 정확히 하나여야 합니다")
            continue
        try:
            intent = yaml.safe_load(blocks[0])
        except yaml.YAMLError as exc:
            errors.append(f"{heading_id}: YAML 오류: {exc}")
            continue
        if not isinstance(intent, dict):
            errors.append(f"{heading_id}: Intent는 mapping이어야 합니다")
            continue
        if heading_id != intent.get("id"):
            errors.append(f"{heading_id}: 제목 ID와 id 필드가 다릅니다")
        if heading_id in intents:
            errors.append(f"{heading_id}: 중복 Intent ID")
        else:
            intents[heading_id] = intent
    if errors:
        raise SchemaError(errors)
    return intents


def load_golden(path) -> dict:
    path = Path(path)
    if not path.exists():
        return {"schema_version": 1, "cases": []}
    data = _read_yaml(path)
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("cases"), list):
        raise SchemaError([f"{path}: schema_version: 1과 cases 목록이 필요합니다"])
    return data


def load_decisions(path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    data = _read_yaml(path)
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("decisions"), list):
        raise SchemaError([f"{path}: schema_version: 1과 decisions 목록이 필요합니다"])
    if not all(isinstance(row, dict) for row in data["decisions"]):
        raise SchemaError([f"{path}: decisions 항목은 mapping이어야 합니다"])
    return data["decisions"]


def load_runs(eval_dir) -> list[dict]:
    runs_dir = Path(eval_dir) / "runs"
    if not runs_dir.exists():
        return []
    runs = []
    for path in runs_dir.glob("*/results.json"):
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SchemaError([f"{path}: {exc}"]) from exc
        if not isinstance(result, dict) or not isinstance(result.get("started_at"), str):
            raise SchemaError([f"{path}: started_at 문자열이 필요합니다"])
        runs.append(result)
    return sorted(runs, key=lambda run: run["started_at"])


def _json_date(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"JSON으로 직렬화할 수 없는 값: {type(value).__name__}")


def canonical_hash(case: dict, fixture_bytes: bytes | None) -> str:
    content = {key: value for key, value in case.items() if key != "batch_id"}
    serialized = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=_json_date)
    fixture_digest = hashlib.sha256(fixture_bytes).hexdigest() if fixture_bytes is not None else ""
    return hashlib.sha256((serialized + fixture_digest).encode("utf-8")).hexdigest()


def case_hash(case: dict, root) -> str:
    fixture = case.get("input", {}).get("fixture")
    fixture_bytes = None
    if fixture:
        path = Path(root) / fixture
        try:
            fixture_bytes = path.read_bytes()
        except FileNotFoundError as exc:
            raise FixtureMissing(str(path)) from exc
    return canonical_hash(case, fixture_bytes)


def case_state(case_id: str, current_hash: str, decisions: list[dict]) -> str:
    relevant = sorted((row for row in decisions if row.get("gs_id") == case_id), key=lambda row: row["seq"])
    if any(row.get("verb") == "RETIRE" for row in relevant):
        return "RETIRED"
    approvals = [row for row in relevant if row.get("verb") == "APPROVE"]
    if not approvals:
        return "APPROVAL_MISSING"
    latest = approvals[-1]
    if latest.get("content_sha256") != current_hash:
        return "APPROVAL_STALE"
    for error in (row for row in relevant if row.get("verb") == "GS_ERROR"):
        approved_before = [row for row in approvals if row["seq"] < error["seq"]]
        if approved_before and approved_before[-1].get("content_sha256") == current_hash:
            return "REDESIGN"
    return "APPROVED"


def _core_intents(intents, target=None):
    for intent_id in sorted(intents):
        intent = intents[intent_id]
        targets = intent.get("target") or []
        if (intent.get("work_type") == "HARNESS" and intent.get("priority") == "CORE"
                and targets and (target is None or target in targets)):
            yield intent_id, intent


def pick_draft_targets(intents, golden, limit=4, target=None) -> list[str]:
    return [intent_id for intent_id, intent in _core_intents(intents, target)
            if not intent.get("golden_set_links")][:limit]


def status_counts(intents, golden, decisions, root, target=None) -> dict:
    core = list(_core_intents(intents, target))
    with_gs = sum(bool(intent.get("golden_set_links")) for _, intent in core)
    relevant_ids = {intent_id for intent_id, _ in core}
    states = {}
    for case in golden["cases"]:
        if case.get("intent_id") not in relevant_ids:
            continue
        state = case_state(case["id"], case_hash(case, root), decisions)
        states[state] = states.get(state, 0) + 1
    return {
        "core_total": len(core), "core_with_gs": with_gs,
        "core_without_gs": len(core) - with_gs,
        "recommended_not_created": sum(bool(intent.get("gs_recommended")) and not intent.get("golden_set_links") for _, intent in core),
        "states": states,
    }
