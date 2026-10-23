---
name: harness-eval-starter
description: 기능·하네스 작업 시작 시 Intent 기록, 「intent 에 ~ 추가해」, 「INT-xxx ~ 바꿔」, 「<하네스> eval 하자」, 「실행 <범위>」, Intent 정리와 현황 질문에 사용하는 스킬.
---

# Harness Eval Starter

사람의 승인·실행·판정은 사람의 직접 입력으로만 기록한다. 골든셋 파일은 사례 내용이고 `eval/decisions.yaml`은 `human_gate.py`만 쓰는 결정 대장이다. 필드와 형식은 [schema.md](references/schema.md), 단계와 비용은 [concepts.md](references/concepts.md)를 따른다.

## Intent 기록

새 기능·하네스 작업을 시작하면 **구현 전**에 Intent 1건을 `eval/intent.md`에 기록한다. 「intent 에 ~ 추가해」·「Intent 등록해 줘」 같은 명시 명령은 작업 요청이 없어도 언제든 처리한다. 사용자 원문을 보존하고, 제품 기능인지 하네스인지, 하네스 target은 무엇인지, 깨지면 하네스가 있을 이유가 없는 CORE인지, 코드로 판정 가능한지의 네 질문 중 원문으로 알 수 없는 것만 묻는다. FEATURE는 첫 질문만 적용한다. 사람이 우선순위를 말했으면 `priority_confirmed: true`로 기록한다. [intent-governance.md](references/intent-governance.md)를 따른다.

「INT-003 CORE 로 바꿔」 또는 target 변경 명령은 지정한 필드만 고치고 history에 `UPDATED` 한 줄을 추가한다. ID·원문·다른 필드·골든셋 링크를 보존한다. 이 단계에서는 골든셋을 묻지도 만들지도 않는다.

## 「<하네스> eval 하자」

`python .claude/skills/harness-eval-starter/scripts/validate_golden.py --draft-targets --target <하네스>`로 CORE이면서 골든셋이 없는 Intent를 고른다. 한 배치 최대 4건으로 DRAFT 사례를 바로 작성한다. `gs_recommended`에 배치 ID를, history에 `RECOMMENDED`를 남기고 실제 생성 사례를 `golden_set_links`에 연결한다. 검수에서 뺀 사례는 재추천하지 않는다. 한 배치 최대 4건, 누적은 무제한이다. [selection-flow.md](references/selection-flow.md)와 [cases.md](references/cases.md)를 따른다.

`python .claude/skills/harness-eval-starter/scripts/validate_golden.py` 검증을 통과한 뒤 사례별 카드와 규칙 원문을 제시한다. 검수 중 수정은 선택형으로 받아도 된다. 최종 검수 뒤 `python .claude/skills/harness-eval-starter/scripts/validate_golden.py --request-block B-xxx` 출력을 **그대로** 붙여 승인 요청한다. 사람이 「승인 모두 / 승인 1,2,4 / 승인 GS-xxx」를 직접 입력해야 한다. 선택 화면 답은 승인이 아니다. 승인 요청 블록이 없거나 지문이 바뀌면 결정 훅이 거부한다.

## 실행 <범위>

사람이 「실행 GS-005」·「실행 <하네스>」·「실행 CORE」처럼 범위를 입력하면 결정 훅이 RUN seq를 낸다. 범위 없는 「실행」과 전부 실행은 없다. 그 seq로 `python .claude/skills/harness-eval-starter/scripts/run_eval.py --request <seq> --dry-run`을 먼저 실행해 대상과 예상 호출 수를 보여준 다음, 같은 `--request <seq>`로 실행한다. RUN 기록의 범위·횟수·ablate를 바꾸지 않는다. 결과 뒤 `python .claude/skills/harness-eval-starter/scripts/eval_summary.py`를 실행하고 사람이 「동의 / 비동의 / 판정 / 골든셋오류 / 폐기」를 입력하도록 안내한다.

**비용 원칙:** 골든셋은 자주 만들고 실측은 가끔 고른 것만 한다. SCRIPT 1건은 AI 호출 0회·수 초다. LLM_JUDGE 1건 1회는 `claude -p` 2회(실행 + 판정), 약 1~2분·약 $0.3이다. 3회 다수결은 비용 약 3배, ablation은 약 2배다. 실습 기본은 승인까지 진행하고 1~2건만 1회 실행한다. 자세한 실측 근거는 [concepts.md](references/concepts.md)에 있다.

## Intent 정리 · 현황 질문

「핵심 인텐트 중 골든셋 없는 게 몇 개?」에는 `python .claude/skills/harness-eval-starter/scripts/validate_golden.py --status`를 실행해 CORE 전체·골든셋 있음·없음·추천됐으나 미생성 수를 그대로 답한다. 현황 질문은 초안을 만들지 않는다. Intent 정리 요청에는 기록된 Intent와 이력을 읽고 정리 대상을 제시한다.

## 설치

대상 프로젝트의 `.claude/skills/harness-eval-starter/`에 이 스킬 폴더를 복사한다. `assets/hooks.settings.json`의 두 훅 항목을 대상 `.claude/settings.json`에 병합한다. `python -m pip install pyyaml`을 실행하고 git `user.name`을 설정한다. 대상 `CLAUDE.md`에 한 줄을 넣는다: 「새 기능 · 하네스 작업을 시작하면 구현 전에 harness-eval-starter 로 Intent 를 기록한다」.
