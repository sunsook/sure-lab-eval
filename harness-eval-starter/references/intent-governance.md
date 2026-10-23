# Intent 기록과 변경

기능·하네스 작업 시작 전에 1건을 기록한다. 「intent 에 ~ 추가해」 같은 명시 명령은 언제든 처리한다. `eval/intent.md`의 제목과 필드 형식은 [schema.md](schema.md)를 따른다. 사용자 원문과 출처를 보존한다.

분류 질문은 ① 제품 기능인가 하네스인가 ② 하네스 target은 어디인가 ③ 깨지면 하네스가 있을 이유가 없는 CORE인가 ④ 코드로 판정 가능한가다. 원문에서 알 수 없는 것만 묻고, FEATURE에는 첫 질문만 적용한다. 사람이 우선순위를 말했으면 `priority_confirmed: true`로 기록한다. target이 없는 HARNESS Intent는 `INTENT_GAP`으로 드러낸다.

「INT-xxx CORE 로 바꿔」·「INT-xxx target 을 ~ 로」는 지정 필드만 바꾸고 history에 `UPDATED`를 append한다. ID·원문·다른 필드·골든셋 링크를 보존한다. 우선순위 변경은 사례 내용 해시를 바꾸지 않는다. 어떤 Intent 기록 경로에서도 골든셋을 묻거나 만들지 않는다.
