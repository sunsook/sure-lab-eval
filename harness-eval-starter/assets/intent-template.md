# Intent 예시

### INT-001

```yaml
id: INT-001
intent: "리뷰 지적에는 파일:줄 근거를 단다"
source: "2026-10-21 사용자 발화"
work_type: HARNESS
target:
  - .claude/rules/review.md
priority: CORE
priority_confirmed: true
eval_type: NON_DETERMINISTIC
gs_recommended: []
golden_set_links: []
history:
  - date: "2026-10-21"
    event: CREATED
    note: "사용자 발화로 등록"
```

append-only 는 지침이다(검증 없음). 변경은 원문과 ID를 보존하고 history에 추가한다.
