# 골든셋 사례 작성과 카드 검수

한 배치 최대 4건, 누적은 무제한이다. 사례 필드는 [schema.md](schema.md)를 따른다. 실제 target에 있는 규칙만 사례로 만들고 `rule_text`는 원문 그대로 적는다. FEATURE Intent나 target에 없는 규칙은 `validate_golden.py`가 거부한다. `SCRIPT`는 명령과 기대 종료 코드를, `LLM_JUDGE`는 프롬프트와 판정 기준을 적는다. fixture를 쓰면 파일 바이트도 사례 해시에 들어간다.

카드는 `사례 ID | Intent | 규칙 원문(출처) | 입력 요약 | 기대 행동 | 확인할 증거`를 보여준다. 검수 중 수정은 허용하지만 승인 요청 직전에 다시 검증한다. `validate_golden.py --request-block B-xxx`의 출력 블록을 그대로 보여주고 사람의 직접 승인 입력을 기다린다. 선택 화면 답은 승인이 아니다. 내용이 바뀌면 기존 승인은 `APPROVAL_STALE`로 계산되어 재승인이 필요하다.

실행 판정은 `PASS`, `FAIL`, `REVIEW`, `ERROR`, `NOT_RUN`을 구분한다. 승인 없는 사례는 집계 분모에 넣지 않는다. 골든셋오류는 사람의 `NOT_HARNESS`, `OFF_INTENT`, `UNJUDGEABLE` 결정으로 `REDESIGN` 대상이 된다.
