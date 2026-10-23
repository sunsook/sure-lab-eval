# Eval 흐름 ② 골든셋 초안 · ③ 검수와 승인

사용자가 「<하네스> eval 하자」라고 하면 `validate_golden.py --draft-targets --target <하네스>`로 CORE이며 골든셋 없는 Intent를 찾는다. 한 배치 최대 4건을 DRAFT로 바로 작성한다. `gs_recommended`에 배치 ID와 history `RECOMMENDED`를 남기고, 만든 사례 ID를 `golden_set_links`에 연결한다. 검수에서 제외한 만큼 줄어들며 재추천하지 않는다. 한 배치 최대 4건, 누적은 무제한이다. 필드 형식은 [schema.md](schema.md)를 따른다.

각 카드에 핵심 의도·실패 상황·입력·기대 행동·관찰 증거·규칙 원문과 출처를 보여준다. 불명확한 부분은 검수 중 선택형으로 고쳐도 된다. `validate_golden.py` 검증을 통과한 사례만 승인 요청한다. `validate_golden.py --request-block B-xxx`의 출력을 그대로 붙인다. 사람은 「승인 모두 / 승인 1,2,4 / 승인 GS-xxx」를 직접 입력한다. 선택 화면 답은 승인이 아니다. 결정 훅이 직전 assistant 블록과 지문을 확인해 `eval/decisions.yaml`에 기록한다. 승인 전 실행하지 않는다.
