# 대상 프로젝트 설치와 보호 경계

스킬을 `.claude/skills/harness-eval-starter/`에 복사하고 [hooks.settings.json](../assets/hooks.settings.json)의 두 훅을 `.claude/settings.json`에 병합한다. UserPromptSubmit의 `human_gate.py`가 사람 결정만 `eval/decisions.yaml`에 쓰며, PreToolUse의 `guard.py`가 대장·실행 결과·보호 스크립트에 대한 AI 도구 쓰기를 막는다. `python -m pip install pyyaml`과 git `user.name` 설정도 필요하다. 대상 `CLAUDE.md`에 「새 기능 · 하네스 작업을 시작하면 구현 전에 harness-eval-starter 로 Intent 를 기록한다」를 넣는다.

`eval/intent.md`, `eval/golden-set.yaml`, `eval/decisions.yaml`, `eval/runs/`의 필드와 쓰기 주체는 [schema.md](schema.md)를 따른다. 초안 뒤 `validate_golden.py`로 검사하고 `--request-block B-xxx`로 승인 요청 블록을 만든다. 사람의 RUN 기록이 있어야 `run_eval.py --request <seq>`가 실행된다. `--dry-run`으로 먼저 예상 호출 수를 확인한다. 한 배치 최대 4건, 누적은 무제한이다.
