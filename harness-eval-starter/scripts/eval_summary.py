"""Summarize one run without changing its results or human decisions."""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

from golden_lib import (FixtureMissing, SchemaError, YamlMissing, case_hash, case_state,
                        load_decisions, load_golden, load_runs)
from run_eval import attribution


GS_ID = re.compile(r"^GS-\d{3}$")
VERDICTS = {"PASS", "FAIL", "REVIEW", "ERROR", "NOT_RUN"}
METHODS = {"SCRIPT", "LLM_JUDGE"}


def _say(message: str, *, error: bool = False) -> None:
    (sys.stderr if error else sys.stdout).buffer.write((message + "\n").encode("utf-8"))


def _checked_rows(run: dict) -> tuple[dict, dict]:
    if not isinstance(run.get("cases"), list):
        raise ValueError("results.cases 목록이 필요합니다")
    with_rows, ablated = {}, {}
    for row in run["cases"]:
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                or not GS_ID.fullmatch(row["id"]) or row.get("method") not in METHODS
                or row.get("condition") not in ("WITH", "ABLATED")
                or row.get("verdict") not in VERDICTS
                or not isinstance(row.get("content_sha256"), str)):
            raise ValueError(f"잘못된 result 사례: {row}")
        destination = with_rows if row["condition"] == "WITH" else ablated
        if row["id"] in destination:
            raise ValueError(f"중복 result 사례: {row['id']} {row['condition']}")
        destination[row["id"]] = row
    return with_rows, ablated


def _approval_at_run(gs_id: str, digest: str, request_seq: int, decisions: list[dict]) -> bool:
    approvals = [row for row in decisions if row.get("verb") == "APPROVE"
                 and row.get("gs_id") == gs_id and row["seq"] < request_seq]
    return bool(approvals and max(approvals, key=lambda row: row["seq"]).get("content_sha256") == digest)


def _last_confirmation(gs_id: str, run_id: str, decisions: list[dict]) -> dict | None:
    rows = [row for row in decisions if row.get("gs_id") == gs_id
            and row.get("run_id") == run_id and row.get("verb") in ("AGREE", "DISAGREE", "VERDICT")]
    return max(rows, key=lambda row: row["seq"]) if rows else None


def _final_verdict(original: str, confirmation: dict | None) -> str:
    if confirmation is None or confirmation["verb"] == "AGREE":
        return original
    if confirmation["verb"] == "DISAGREE":
        if original not in ("PASS", "FAIL"):
            raise ValueError("REVIEW/ERROR에는 DISAGREE를 적용할 수 없습니다")
        return "FAIL" if original == "PASS" else "PASS"
    value = confirmation.get("value")
    if value not in ("PASS", "FAIL"):
        raise ValueError("VERDICT 값은 PASS 또는 FAIL이어야 합니다")
    return value


def _agreement(runs: list[dict], decisions: list[dict], *, only_run: str | None = None) -> tuple[int, int]:
    agreed = confirmed = 0
    for run in runs:
        if only_run is not None and run["run_id"] != only_run:
            continue
        with_rows, _ = _checked_rows(run)
        for gs_id, row in with_rows.items():
            if row["method"] != "LLM_JUDGE":
                continue
            decision = _last_confirmation(gs_id, run["run_id"], decisions)
            if decision and decision["verb"] in ("AGREE", "DISAGREE"):
                confirmed += 1
                agreed += decision["verb"] == "AGREE"
    return agreed, confirmed


def summarize(root: Path, selected_run: str | None = None) -> list[str]:
    eval_dir = root / "eval"
    runs = load_runs(eval_dir)
    if not runs:
        raise ValueError("실행 기록이 없습니다")
    run = next((item for item in runs if item.get("run_id") == selected_run), None) if selected_run else runs[-1]
    if run is None:
        raise ValueError(f"run을 찾을 수 없습니다: {selected_run}")
    if not isinstance(run.get("request_seq"), int) or not isinstance(run.get("run_id"), str):
        raise ValueError("run_id와 request_seq가 필요합니다")
    decisions = load_decisions(eval_dir / "decisions.yaml")
    golden = load_golden(eval_dir / "golden-set.yaml")
    cases = {case["id"]: case for case in golden["cases"]}
    with_rows, ablated = _checked_rows(run)
    method_counts = {method: Counter() for method in METHODS}
    contribution = Counter()
    excluded = Counter()
    case_lines = []
    for gs_id, row in with_rows.items():
        case = cases.get(gs_id)
        if case is None:
            raise ValueError(f"골든셋에 없는 결과: {gs_id}")
        digest = case_hash(case, root)
        state_at_request = case_state(gs_id, digest, [d for d in decisions if d["seq"] < run["request_seq"]])
        if row["content_sha256"] != digest:
            excluded["STALE"] += 1
            continue
        if not _approval_at_run(gs_id, digest, run["request_seq"], decisions):
            excluded["APPROVAL_MISSING" if state_at_request == "APPROVAL_MISSING" else "STALE"] += 1
            continue
        state = case_state(gs_id, digest, decisions)
        if state != "APPROVED":
            excluded["STALE" if state == "APPROVAL_STALE" else state] += 1
            continue
        if row["method"] != case["method"]:
            raise ValueError(f"방법 불일치: {gs_id}")
        confirmation = _last_confirmation(gs_id, run["run_id"], decisions)
        final = _final_verdict(row["verdict"], confirmation)
        case_lines.append(f"{gs_id}: {final}")
        method_counts[row["method"]]["total"] += 1
        method_counts[row["method"]][final] += 1
        if gs_id in ablated:
            if ablated[gs_id]["content_sha256"] != digest:
                raise ValueError(f"ABLATED 지문 불일치: {gs_id}")
            category, _ = attribution(final, ablated[gs_id]["verdict"])
            if category:
                contribution[category] += 1
    for gs_id, case in cases.items():
        if gs_id in with_rows:
            continue
        state = case_state(gs_id, case_hash(case, root), decisions)
        excluded["NOT_RUN" if state == "APPROVED" else
                 "STALE" if state == "APPROVAL_STALE" else state] += 1
    current_agree, current_confirmed = _agreement(runs, decisions, only_run=run["run_id"])
    all_agree, all_confirmed = _agreement(runs, decisions)
    script, judge = method_counts["SCRIPT"], method_counts["LLM_JUDGE"]
    return [" | ".join(case_lines),
            f"SCRIPT       {script['PASS']}/{script['total']} PASS",
            f"LLM_JUDGE    {judge['PASS']}/{judge['total']} PASS · REVIEW={judge['REVIEW']} · ERROR={judge['ERROR']}",
            f"기여         ATTRIBUTED={contribution['ATTRIBUTED']} · NOT_ATTRIBUTED={contribution['NOT_ATTRIBUTED']}",
            f"judge 일치율  동의 {current_agree} / 확인 {current_confirmed}",
            f"judge 일치율(누적)  동의 {all_agree} / 확인 {all_confirmed}",
            "제외         " + " · ".join(f"{name}={excluded[name]}" for name in
                                       ("APPROVAL_MISSING", "STALE", "REDESIGN", "RETIRED", "NOT_RUN"))]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--run")
    args = parser.parse_args()
    try:
        for line in summarize(args.root.resolve(), args.run):
            _say(line)
        return 0
    except YamlMissing:
        _say("PyYAML이 필요합니다: python -m pip install pyyaml", error=True)
        return 3
    except (SchemaError, FixtureMissing, OSError, ValueError, KeyError, TypeError) as exc:
        _say(f"{type(exc).__name__}: {exc}", error=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
