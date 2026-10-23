"""Execute one human RUN request against isolated project copies."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from golden_lib import (FixtureMissing, SchemaError, YamlMissing, case_hash, case_state,
                        load_decisions, load_golden, load_intents, load_runs)
from validate_golden import _schema, validate


def _say(message: str, *, error: bool = False) -> None:
    (sys.stderr if error else sys.stdout).buffer.write((message + "\n").encode("utf-8"))


def _safe_relative(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"프로젝트 밖 경로: {value}")
    return path


def _starter_hook(command: str) -> bool:
    return "human_gate.py" in command or "guard.py" in command


def _settings_target(target: str):
    parts = target.split(":", 2)
    return (parts[1], parts[2]) if len(parts) == 3 and parts[0] == "settings" else None


def _filter_settings(copy: Path, targets: list[str], *, ablated: bool = False) -> None:
    path = copy / ".claude/settings.json"
    if not path.exists():
        return
    settings = json.loads(path.read_text(encoding="utf-8"))
    for event, entries in list(settings.get("hooks", {}).items()):
        kept = []
        for entry in entries:
            hooks = []
            for hook in entry.get("hooks", []):
                command = hook.get("command", "")
                matches = any(event == pair[0] and pair[1] in command
                              for target in targets if (pair := _settings_target(target)))
                if (_starter_hook(command) and not matches) or (ablated and matches):
                    continue
                hooks.append(hook)
            if hooks:
                kept.append({**entry, "hooks": hooks})
        settings["hooks"][event] = kept
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _git(copy: Path, *args: str) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *args], cwd=copy, capture_output=True, encoding="utf-8", check=False)
    if result.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result


def copy_project(root: Path, targets: list[str], *, ablated: bool = False,
                 fixture: str | None = None) -> Path:
    """Copy a project outside its tree, retain case targets, and make a baseline commit."""
    root = Path(root).resolve()
    copy = Path(tempfile.mkdtemp(prefix="harness-eval-"))
    try:
        if copy.is_relative_to(root):
            raise RuntimeError("격리 사본은 저장소 밖이어야 합니다")
        skill_targets = [target for target in targets if "harness-eval-starter" in Path(target).parts]
        def ignore(directory, names):
            hidden = {name for name in names if name == ".git"}
            if Path(directory).resolve() == root:
                hidden.update(name for name in names if name == "eval")
            if Path(directory).name == "skill" and not skill_targets:
                hidden.update(name for name in names if name == "harness-eval-starter")
            if not skill_targets:
                hidden.update(name for name in names if name == "harness-eval-starter" and
                              (Path(directory) / name).is_dir())
            return hidden
        shutil.copytree(root, copy, dirs_exist_ok=True, ignore=ignore)
        for target in skill_targets:
            parts = Path(target).parts
            index = parts.index("harness-eval-starter")
            source = root.joinpath(*parts[:index + 1])
            destination = copy / ".claude/skills/harness-eval-starter"
            shutil.copytree(source, destination, dirs_exist_ok=True)
            copied_source = copy.joinpath(*parts[:index + 1])
            if copied_source.exists() and copied_source != destination:
                shutil.rmtree(copied_source)
        for target in targets:
            pair = _settings_target(target)
            if pair is None:
                continue
            script_name = pair[1]
            for source in root.rglob(script_name):
                relative = source.relative_to(root)
                if not source.is_file() or any(part in (".git", "eval") for part in relative.parts):
                    continue
                for required in (source, source.with_name("golden_lib.py")):
                    if required.exists():
                        destination = copy / required.relative_to(root)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(required, destination)
        _filter_settings(copy, targets, ablated=ablated)
        if fixture:
            fixture_path = _safe_relative(fixture)
            destination = copy / ".eval-input" / fixture_path.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / fixture_path, destination)
        if ablated:
            for target in targets:
                if _settings_target(target):
                    continue
                path = _safe_relative(target)
                destination = (copy / ".claude/skills/harness-eval-starter" / Path(*path.parts[path.parts.index("harness-eval-starter") + 1:])
                               if "harness-eval-starter" in path.parts else copy / path)
                if destination.is_dir():
                    shutil.rmtree(destination)
                else:
                    destination.unlink(missing_ok=True)
        _git(copy, "init", "-q")
        _git(copy, "add", ".")
        _git(copy, "-c", "user.name=eval", "-c", "user.email=eval@local",
             "-c", "core.autocrlf=false", "commit", "-q", "--allow-empty", "-m", "baseline")
        return copy
    except Exception:
        cleanup_copy(copy)
        raise


def cleanup_copy(copy: Path) -> None:
    def writable(function, path, exc):
        os.chmod(path, stat.S_IWRITE)
        function(path)
    try:
        shutil.rmtree(copy, onexc=writable)
    except OSError as exc:
        _say(f"격리 사본 정리 실패: {copy}: {exc}", error=True)


def run_script(case: dict, copy: Path, run_dir: Path, stem: str, *,
               fixture_bytes: bytes | None = None, timeout: float = 120) -> dict:
    command = case["input"]["command"]
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(copy)
    output_path = f"{stem}.stdout.txt"
    error_path = f"{stem}.stderr.txt"
    attempt = {"verdict": "ERROR", "evidence_quote": None, "quote_found": None,
               "exit_code": None, "output_path": output_path}
    try:
        completed = subprocess.run(command, shell=True, cwd=copy,
                                   input=fixture_bytes.decode("utf-8") if fixture_bytes is not None else None,
                                   capture_output=True, timeout=timeout, env=env, encoding="utf-8")
        stdout = completed.stdout
        stderr = completed.stderr
        attempt["exit_code"] = completed.returncode
        criteria = case["judgement_criteria"]
        attempt["verdict"] = ("PASS" if completed.returncode == criteria["exit_code"] and
                              criteria.get("stdout_contains", "") in stdout and
                              criteria.get("stderr_contains", "") in stderr else "FAIL")
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        stderr += "\nTIMEOUT"
    except (OSError, UnicodeError) as exc:
        stdout, stderr = "", f"{type(exc).__name__}: {exc}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / output_path).write_text(stdout, encoding="utf-8")
    (run_dir / error_path).write_text(stderr, encoding="utf-8")
    return attempt


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    return " ".join(value.translate(str.maketrans("", "", "`*_" )).split())


def _decoded_record(raw: str) -> tuple[str, int]:
    lines, bad = [], 0
    for line in raw.splitlines():
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError("stream event is not an object")
        except (json.JSONDecodeError, ValueError):
            bad += 1
            continue
        message = event.get("message") or {}
        if not isinstance(message, dict):
            continue
        role = message.get("role", event.get("type"))
        for block in message.get("content", []):
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if role == "assistant" and kind == "text":
                lines.append(f"[assistant] {block.get('text', '')}")
            elif role == "assistant" and kind == "tool_use":
                payload = json.dumps(block.get("input", {}), ensure_ascii=False)
                lines.append(f"[tool_use {block.get('name', '')}] {payload}")
            elif kind == "tool_result":
                content = block.get("content", "")
                if isinstance(content, list):
                    content = "\n".join(item.get("text", "") for item in content if isinstance(item, dict))
                lines.append(f"[tool_result] {content}")
    return "\n".join(lines), bad


def _first_object(value: str) -> dict:
    decoder = json.JSONDecoder()
    for position, char in enumerate(value):
        if char == "{":
            try:
                item, _ = decoder.raw_decode(value[position:])
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                return item
    raise ValueError("판정 JSON 객체 없음")


def run_llm_judge(case: dict, copy: Path, run_dir: Path, stem: str,
                  model: str | None, *, timeout: float = 180) -> dict:
    run_dir.mkdir(parents=True, exist_ok=True)
    raw_name, decoded_name, judge_name = f"{stem}.stream.jsonl", f"{stem}.decoded.txt", f"{stem}.judge.json"
    attempt = {"verdict": "ERROR", "evidence_quote": None, "quote_found": None,
               "exit_code": None, "output_path": raw_name, "decoded_path": decoded_name,
               "judge_path": judge_name, "parse_errors": 0}
    claude = shutil.which("claude")
    if not claude:
        attempt["reason"] = "claude 를 PATH 에서 찾지 못함 — 설치 · 로그인 확인"
        return attempt
    env = {key: value for key, value in os.environ.items()
           if not (key.startswith("CLAUDE_CODE_") or key in ("CLAUDE_EFFORT", "CLAUDECODE"))}
    settings = {"claudeMdExcludes": [str(Path.home() / ".claude/CLAUDE.md"),
                                      str(Path.home() / ".claude/rules/**")]}
    exec_args = [claude, "-p", "--output-format", "stream-json", "--verbose",
                 "--permission-mode", "acceptEdits", "--setting-sources", "project,local",
                 "--settings", json.dumps(settings, ensure_ascii=False), "--strict-mcp-config"]
    if model:
        exec_args.extend(["--model", model])
    stage = "execution"
    try:
        executed = subprocess.run(exec_args, cwd=copy, input=case["input"]["prompt"],
                                  capture_output=True, encoding="utf-8", timeout=timeout, env=env)
        attempt["exit_code"] = executed.returncode
        (run_dir / raw_name).write_text(executed.stdout, encoding="utf-8")
        (run_dir / f"{stem}.stream.stderr.txt").write_text(executed.stderr, encoding="utf-8")
        record, bad = _decoded_record(executed.stdout)
        attempt["parse_errors"] = bad
        (run_dir / decoded_name).write_text(record + ("\n" if record else ""), encoding="utf-8")
        if executed.returncode or not record:
            attempt["reason"] = f"실행 실패(exit {executed.returncode}; 파싱 실패 {bad}줄; 기록 없음={not bool(record)})"
            return attempt
        prompt = (f"판정 기준: {case['judgement_criteria']}\n"
                  f"기대 행동: {case['expected_behavior']}\n"
                  f"관찰 증거: {case['observable_evidence']}\n"
                  f"실행 기록:\n{record}\n"
                  'JSON 한 줄만: {"verdict":"PASS|FAIL","evidence_quote":"…","reason":"…"}. '
                  "evidence_quote 는 실행 기록에서 연속된 한 구절을 그대로 옮기고 여러 구절을 이어 붙이지 말 것")
        judge_args = [claude, "-p", "--output-format", "json", "--safe-mode",
                      "--strict-mcp-config", "--tools", ""]
        stage = "judge"
        judged = subprocess.run(judge_args, cwd=copy, input=prompt, capture_output=True,
                                encoding="utf-8", timeout=timeout, env=env)
        (run_dir / judge_name).write_text(judged.stdout, encoding="utf-8")
        (run_dir / f"{stem}.judge.stderr.txt").write_text(judged.stderr, encoding="utf-8")
        if judged.returncode:
            attempt["reason"] = f"판정 호출 실패(exit {judged.returncode})"
            return attempt
        response = json.loads(judged.stdout)
        if not isinstance(response, dict) or not isinstance(response.get("result"), str):
            raise ValueError("판정 응답 result 문자열 없음")
        verdict = _first_object(response["result"])
        if verdict.get("verdict") not in ("PASS", "FAIL"):
            raise ValueError("판정 verdict 오류")
        quote = verdict.get("evidence_quote")
        if not isinstance(quote, str) or not quote.strip():
            raise ValueError("판정 evidence_quote 누락")
        attempt["evidence_quote"] = quote
        attempt["quote_found"] = normalize(quote) in normalize(record)
        attempt["verdict"] = verdict["verdict"] if attempt["quote_found"] else "REVIEW"
        attempt["reason"] = verdict.get("reason")
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout or b""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", errors="replace")
        (run_dir / (raw_name if stage == "execution" else judge_name)).write_text(partial, encoding="utf-8")
        attempt["reason"] = f"{stage} timeout: {exc}"
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        attempt["reason"] = f"{type(exc).__name__}: {exc}"
    return attempt


def attribution(with_verdict: str, ablated_verdict: str) -> tuple[str | None, str | None]:
    if with_verdict == "PASS" and ablated_verdict == "FAIL":
        return "ATTRIBUTED", None
    if with_verdict == "PASS" and ablated_verdict == "PASS":
        return "NOT_ATTRIBUTED", None
    if with_verdict == "FAIL" and ablated_verdict in ("PASS", "FAIL"):
        return None, None
    return None, f"기여 계산 불가: WITH={with_verdict}, ABLATED={ablated_verdict}"


def _selected(case: dict, intent: dict, scope: dict) -> bool:
    if "cases" in scope:
        return case["id"] in scope["cases"]
    if "target" in scope:
        return scope["target"] in intent.get("target", [])
    if scope == {"priority": "CORE"}:
        return intent.get("priority") == "CORE"
    raise ValueError("RUN 범위가 필요합니다: cases / target / priority CORE")


def _verdict(attempts: list[dict]) -> str:
    quorum = len(attempts) // 2 + 1
    valid = sum(item["verdict"] in ("PASS", "FAIL", "REVIEW") for item in attempts)
    if valid < quorum:
        return "ERROR"
    passes = sum(item["verdict"] == "PASS" for item in attempts)
    fails = sum(item["verdict"] == "FAIL" for item in attempts)
    if passes >= quorum:
        return "PASS"
    if fails >= quorum:
        return "FAIL"
    return "REVIEW"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True, type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--model")
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        eval_dir = root / "eval"
        decisions = load_decisions(eval_dir / "decisions.yaml")
        request = next((row for row in decisions if row.get("seq") == args.request and row.get("verb") == "RUN"), None)
        if request is None:
            _say("사람이 실행 <범위> 를 입력해야 돈다")
            return 1
        if any(run.get("request_seq") == args.request for run in load_runs(eval_dir)):
            _say("소진된 실행 요청")
            return 1
        scope = request.get("scope")
        if not isinstance(scope, dict) or len(scope) != 1 or not any(k in scope for k in ("cases", "target", "priority")):
            _say("사람이 실행 <범위> 를 입력해야 돈다")
            return 1
        intents = load_intents(eval_dir / "intent.md")
        golden = load_golden(eval_dir / "golden-set.yaml")
        errors = validate(root, intents, golden, _schema())
        if errors:
            for error in errors:
                _say(error)
            return 1
        chosen, excluded = [], {}
        for case in golden["cases"]:
            intent = intents[case["intent_id"]]
            if not _selected(case, intent, scope):
                continue
            digest = case_hash(case, root)
            state = case_state(case["id"], digest, decisions)
            if state == "APPROVED":
                chosen.append((case, intent, digest))
            else:
                excluded[state] = excluded.get(state, 0) + 1
        for state, count in sorted(excluded.items()):
            _say(f"제외 {state}={count}")
        value = request.get("value") or {}
        runs, ablate = value.get("runs", 1), value.get("ablate", False)
        if not isinstance(runs, int) or runs < 1 or not isinstance(ablate, bool):
            raise ValueError("RUN 횟수 또는 ablate 값 오류")
        calls = sum(2 * runs * (2 if ablate else 1) for case, _, _ in chosen if case["method"] == "LLM_JUDGE")
        _say("대상: " + (", ".join(case["id"] for case, _, _ in chosen) or "없음"))
        if args.dry_run:
            _say(f"예상 claude 호출 수: {calls}; LLM_JUDGE 1건 1회 2호출 ≈ 1~2분")
            return 0
        started = datetime.now(timezone.utc)
        run_base = "R-" + started.strftime("%Y%m%d-%H%M%S")
        run_dir = eval_dir / "runs" / run_base
        suffix = 2
        while run_dir.exists():
            run_dir = eval_dir / "runs" / f"{run_base}-{suffix}"
            suffix += 1
        run_dir.mkdir(parents=True)
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, encoding="utf-8")
        result = {"run_id": run_dir.name, "request_seq": args.request,
                  "harness_sha": sha.stdout.strip() if sha.returncode == 0 else "nogit",
                  "started_at": started.isoformat(), "cases": []}
        for case, intent, digest in chosen:
            fixture = case["input"].get("fixture")
            fixture_bytes = (root / _safe_relative(fixture)).read_bytes() if fixture else None
            for condition in (["WITH", "ABLATED"] if ablate else ["WITH"]):
                attempts = []
                for number in range(1, runs + 1):
                    copy = None
                    try:
                        copy = copy_project(root, intent["target"], ablated=condition == "ABLATED", fixture=fixture)
                        if case["method"] == "SCRIPT":
                            attempt = run_script(case, copy, run_dir, f"{case['id']}-{condition}-{number}", fixture_bytes=fixture_bytes)
                        else:
                            attempt = run_llm_judge(case, copy, run_dir,
                                                    f"{case['id']}-{condition}-{number}", args.model)
                    except (OSError, RuntimeError, ValueError) as exc:
                        attempt = {"verdict": "ERROR", "evidence_quote": None, "quote_found": None,
                                   "exit_code": None, "output_path": None, "reason": f"{type(exc).__name__}: {exc}"}
                    finally:
                        if copy is not None:
                            cleanup_copy(copy)
                    attempts.append(attempt)
                result["cases"].append({"id": case["id"], "method": case["method"],
                                        "condition": condition, "content_sha256": digest,
                                        "attempts": attempts, "verdict": _verdict(attempts), "attribution": None})
        by_id = {}
        for row in result["cases"]:
            by_id.setdefault(row["id"], {})[row["condition"]] = row
        for gs_id, pair in by_id.items():
            if "ABLATED" in pair:
                value, reason = attribution(pair["WITH"]["verdict"], pair["ABLATED"]["verdict"])
                pair["WITH"]["attribution"] = value
                if reason:
                    pair["WITH"]["attribution_reason"] = reason
                if value == "NOT_ATTRIBUTED":
                    _say(f"골든셋오류 후보(OFF_INTENT?) {gs_id}")
        (run_dir / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _say(f"결과: {run_dir / 'results.json'}")
        _say("동의 / 비동의 / 판정 / 골든셋오류 를 프롬프트로 입력하세요")
        if len(load_runs(eval_dir)) == 1:
            _say("첫 eval: judge 판정을 훑어보고 동의/비동의를 남기세요(보정)")
        return 0
    except YamlMissing:
        _say("PyYAML이 필요합니다: python -m pip install pyyaml", error=True)
        return 3
    except (SchemaError, FixtureMissing, OSError, ValueError, RuntimeError) as exc:
        _say(f"{type(exc).__name__}: {exc}", error=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
