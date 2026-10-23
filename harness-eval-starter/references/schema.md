# Starter schema

이 문서가 starter의 필드·형식 단일 정의다. 아래 `yaml schema` 블록은 기계가 읽는 목록이며, 표는 작성과 검수에 쓰는 설명이다. 대상 프로젝트의 `eval/` 파일을 기술한다.

```yaml schema
intent_fields: [id, intent, source, work_type, target, priority, priority_confirmed, eval_type, gs_recommended, golden_set_links, history]
case_fields: [id, intent_id, batch_id, core_intent, failure_situation, input, expected_behavior, observable_evidence, method, judgement_criteria, rule_source, rule_text]
hash_excluded: [batch_id]
decision_fields: [seq, at, verb, gs_id, scope, run_id, content_sha256, value, approver, prompt_excerpt]
decision_optional_fields: [reason]
decision_verbs: [APPROVE, RUN, AGREE, DISAGREE, VERDICT, GS_ERROR, RETIRE]
result_fields: [run_id, request_seq, harness_sha, started_at, cases]
result_case_fields: [id, method, condition, content_sha256, attempts, verdict, attribution]
attempt_fields: [verdict, evidence_quote, quote_found, exit_code, output_path]
enums:
  work_type: [FEATURE, HARNESS]
  priority: [CORE, NORMAL]
  eval_type: [DETERMINISTIC, NON_DETERMINISTIC]
  method: [SCRIPT, LLM_JUDGE]
  condition: [WITH, ABLATED]
  verdict: [PASS, FAIL, REVIEW, ERROR, NOT_RUN]
  attribution: [ATTRIBUTED, NOT_ATTRIBUTED]
  gs_error: [NOT_HARNESS, OFF_INTENT, UNJUDGEABLE]
  state: [APPROVAL_MISSING, APPROVAL_STALE, REDESIGN, RETIRED, APPROVED]
intent_id_re: '^INT-\d{3}$'
gs_id_re: '^GS-\d{3}$'
batch_id_re: '^B-\d{3}$'
run_id_re: '^R-\d{8}-\d{6}(-\d+)?$'
target_settings_re: '^settings:[A-Za-z]+:.+$'
approval_request_block: '[승인 요청 B-xxx] 다음 줄은 <번호>. GS-xxx · 지문 <canonical_hash 앞 8자> — <core_intent>; 최대 4줄'
approval_header_re: '^\[승인 요청 (B-\d{3})\]$'
approval_line_re: '^(\d+)\. (GS-\d{3}) · 지문 ([0-9a-f]{8}) — (.+)$'
approval_max_lines: 4
script_criteria_keys: [exit_code, stdout_contains, stderr_contains]
```

## Intent: `eval/intent.md`

각 Intent는 `### INT-001` 제목과 `yaml` 블록 한 개로 쓴다. 사용자 원문은 요약하지 않는다. `FEATURE`이면 `target: []`, `priority: null`, `priority_confirmed: false`, `eval_type: null`이고 골든셋 대상이 아니다. `HARNESS`의 target이 없으면 `INTENT_GAP`으로 보고한다.

| 필드 | 값과 의미 |
|---|---|
| `id` | `INT-001` 형식. |
| `intent` | 사용자 발화 원문. |
| `source` | 발화 날짜 또는 문서 경로. |
| `work_type` | `FEATURE` 또는 `HARNESS`; 섞이면 별도 Intent로 나눈다. |
| `target` | 하네스 위치 목록. 파일 경로 또는 `settings:<이벤트>:<명령에 든 스크립트 이름>`(예: `settings:PreToolUse:guard.py`). 보통 한 항목이다. |
| `priority` | `CORE` 또는 `NORMAL`; CORE는 깨지면 하네스가 존재할 이유가 없는 의도. |
| `priority_confirmed` | 사람이 우선순위를 확인했는지 불리언. |
| `eval_type` | `DETERMINISTIC` 또는 `NON_DETERMINISTIC`; 사례의 기본 판정 방법을 정한다. |
| `gs_recommended` | 추천된 배치 ID 목록, 예: `[B-001]`. |
| `golden_set_links` | 실제 생성된 사례 ID 목록, 예: `[GS-001]`; 사례의 `intent_id`와 양방향 일치해야 한다. |
| `history` | `{date, event, note}` 항목의 append-only 목록. `RECOMMENDED`, `NOT_SELECTED_THIS_ROUND`, `GS_CREATED`, `UPDATED` 등을 기록한다. |

등록 시 묻는 분류 질문은 작업이 제품 기능인지 하네스인지, 하네스 target이 무엇인지, CORE인지, 코드로 판정 가능한지의 네 가지다. FEATURE는 첫 질문만 적용한다.

## 골든셋: `eval/golden-set.yaml`

최상위는 `schema_version: 1`과 누적 `cases` 목록이다. 한 `batch_id`당 사례는 최대 4개이고 누적 수에는 제한이 없다. 사례에는 수명 상태나 승인자 필드를 쓰지 않는다.

| 필드 | 값과 의미 | 내용 해시 |
|---|---|---|
| `id` | `GS-001` 형식. | 포함 |
| `intent_id` | 연결된 `HARNESS` Intent ID. | 포함 |
| `batch_id` | `B-001` 형식. | 제외 |
| `core_intent` | 핵심 의도. | 포함 |
| `failure_situation` | 실패 상황. | 포함 |
| `input` | `LLM_JUDGE`는 `prompt`, `SCRIPT`는 `command`; `fixture` 경로는 선택. | 포함, fixture 파일 바이트도 포함 |
| `expected_behavior` | 기대 행동. | 포함 |
| `observable_evidence` | 출력이나 로그에서 확인할 증거. | 포함 |
| `method` | `SCRIPT` 또는 `LLM_JUDGE`. | 포함 |
| `judgement_criteria` | SCRIPT는 `exit_code` 필수인 매핑(`stdout_contains`, `stderr_contains` 선택), LLM_JUDGE는 판정 기준 문장 문자열. | 포함 |
| `rule_source` | target 규칙의 파일 경로와 줄. | 포함 |
| `rule_text` | target에 실제 있는 규칙 원문. settings target이면 훅 command 또는 스크립트 본문에 있어야 한다. | 포함 |

내용 해시는 `golden_lib.canonical_hash()`만 만든다. 사례의 `batch_id`를 빼고 키 정렬·UTF-8 보존·공백 없는 JSON 직렬화(`sort_keys=True, ensure_ascii=False, separators=(",", ":")`)를 사용하며, fixture 경로가 있으면 파일 바이트의 SHA-256도 반영한다. fixture가 없으면 승인할 수 없다.

## 결정 대장: `eval/decisions.yaml`

최상위는 `schema_version: 1`과 append-only `decisions` 목록이다. `human_gate.py`만 쓴다. 행의 필드는 아래와 같고, 해당 동사에 적용되지 않는 선택 필드는 비울 수 있다. `approver`는 `git config user.name`이며 빈 값이면 기록하지 않는다.

| 필드 | 의미 |
|---|---|
| `seq` · `at` · `verb` | 순번, 기록 시각, 일곱 결정 동사 중 하나. |
| `gs_id` · `scope` | 사례 결정의 GS ID 또는 RUN의 범위. |
| `run_id` | 사람 확인을 결속할 최신 실행 ID. |
| `content_sha256` | 승인 당시 사례 해시. |
| `value` | 판정값, 골든셋오류 유형 등 동사별 값. |
| `reason` (선택) | `GS_ERROR`를 입력한 사람이 덧붙인 사유 원문. |
| `approver` · `prompt_excerpt` | git 사용자 이름과 프롬프트 일부. |

| 사람 프롬프트의 머리 | `verb` | 효과 |
|---|---|---|
| `승인 모두` · `승인 1,2,4` · `승인 GS-009[,GS-010…]` | `APPROVE` | 직전 assistant의 승인 요청 블록 안에서만 지문과 현재 해시를 대조해 승인한다. 한 건이라도 불일치하면 전체 거부. |
| `실행 GS-005[,GS-007]` · `실행 <하네스 경로>` · `실행 CORE` [`3회`] [`ablate`] | `RUN` | 범위에 승인된(APPROVED) 사례가 있어야 기록한다 — GS ID 지정은 전부 APPROVED. 범위·횟수·ablate를 기록한다. 실행기는 `--request <seq>`만 받고 그 요청보다 먼저 승인된 사례만 돌린다. 승인 전 「실행」은 무효라 승인 뒤 다시 입력한다. |
| `동의 GS-005` | `AGREE` | 최신 run의 판정을 확인한다. |
| `비동의 GS-005` | `DISAGREE` | 최신 run의 PASS/FAIL을 뒤집는다. |
| `판정 GS-005 PASS\|FAIL` | `VERDICT` | REVIEW에 사람 판정을 준다. |
| `골든셋오류 GS-005 NOT_HARNESS\|OFF_INTENT\|UNJUDGEABLE [사유]` | `GS_ERROR` | REDESIGN 대상으로 만든다. |
| `폐기 GS-005` | `RETIRE` | 영구 RETIRED 대상으로 만든다. |

승인 요청 블록은 `[승인 요청 B-xxx]` 머리 줄과 `<번호>. GS-xxx · 지문 <canonical_hash 앞 8자> — <core_intent>` 형식의 사례 줄 최대 4줄이다. 요청 블록을 보이지 않았거나 지문이 바뀌었으면 승인할 수 없다. `APPROVAL_MISSING`, `APPROVAL_STALE`, `REDESIGN`, `RETIRED`, `APPROVED`는 결정 대장과 현재 해시로 계산하며 사례 파일에 쓰지 않는다. `RUN` 소진도 대장에 기록하지 않는다. `results.json` 중 같은 `request_seq`가 있으면 소진된 것이다. 사람 확인은 해당 사례의 최신 `run_id`에만 적용하고 새 run으로 이월하지 않는다. 골든셋오류 · 폐기는 사례 상태(REDESIGN · RETIRED)를 바꾸므로, 집계는 실행 당시 승인(요청 seq 이전)과 별도로 집계 시점 상태를 보고 그 사례를 제외한다.

## 실행 결과: `eval/runs/<run_id>/results.json`

| 수준 | 필드 | 의미 |
|---|---|---|
| run | `run_id`, `request_seq`, `harness_sha`, `started_at`, `cases` | 요청 seq로 실행 결속; `R-YYYYMMDD-HHMMSS` 형식. |
| case | `id`, `method`, `condition`, `content_sha256`, `attempts`, `verdict`, `attribution` | `content_sha256`는 실행 시 사례 해시로, 사람 결정과 run의 결속을 검사한다. `condition`은 `WITH`/`ABLATED`. 기여 미계산은 null. |
| attempt | `verdict`, `evidence_quote`, `quote_found`, `exit_code`, `output_path` | 원 출력은 같은 run 폴더에 보관한다. |

결과 verdict는 `PASS`, `FAIL`, `REVIEW`, `ERROR`, `NOT_RUN` 중 하나다. 실행 오류나 파싱 실패는 `ERROR`로 드러내며 빈 값이나 PASS로 대체하지 않는다.

## 확장 필드

`category` 같은 추가 필드가 필요하면 여기에서 정의한 뒤 버전을 검토한다. 현재 최소 Intent 11필드에는 넣지 않는다.
