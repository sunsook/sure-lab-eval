# sure-lab-eval — Harness Eval Starter (실습 배포판)

> 교육용 배포판 — 개인 용도로 쓰고 재배포는 문의 바랍니다.

하네스가 사람의 의도(Intent)를 지키는지 정답 묶음(Golden Set)으로 재는 Claude Code Skill 이다. 2 · 3일차 실습 2-3 · 2-4 · 3-1 에서 쓴다.

| 쓰는 저장소 | 설치하는 때 |
|---|---|
| 예제코드저장소(lab1-code) | 2-3-준비-1 |
| PR리뷰어저장소(lab2-reviewer) | 2-4-준비-2 |

## 설치 — 대상 저장소 루트에서

1. 이 저장소의 `harness-eval-starter/` 폴더를 대상 저장소 `.claude/skills/harness-eval-starter/` 로 복사한다.
2. `harness-eval-starter/assets/hooks.settings.json` 의 훅 두 항목을 대상 `.claude/settings.json` 의 `hooks` 에 합친다(있는 훅은 지우지 않는다).
3. `python -m pip install pyyaml` 을 실행하고, `git config user.name` 이 비어 있으면 설정한다.
4. 대상 `CLAUDE.md` 에 한 줄을 넣는다 — 「새 기능 · 하네스 작업을 시작하면 구현 전에 harness-eval-starter 로 Intent 를 기록한다」.

확인: `python .claude/skills/harness-eval-starter/scripts/validate_golden.py --status` 가 종료 코드 0 으로 끝난다. 새 Claude Code 세션을 열어 Skill 이 보이는지도 본다.

## 쓰는 법

`harness-eval-starter/SKILL.md` 를 따른다. 「<하네스> eval 하자」 → 초안 검수 → 사람이 「승인 …」 입력 → 「실행 GS-xxx」. 실행은 AI 호출 비용이 든다 — 골든셋은 자주 만들고 실행은 고른 것만 한다.

## 출처

`sun_harness_eval` 커밋 `02c9b47` 의 `skill/harness-eval-starter/` 를 그대로 옮겼다.
