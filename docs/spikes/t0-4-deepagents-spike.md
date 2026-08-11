# T0-4 스파이크 리포트 — deepagents 3대 계약 검증

> 상위: [14-태스크분할](../plan/14-태스크분할.md) §1 T0-4 · [03-멀티에이전트](../plan/03-멀티에이전트.md) §1 반증선
> 검증 코드: [`tests/test_deepagents_spike.py`](../../tests/test_deepagents_spike.py) (CI에서 상시 실행)
> 대상 버전: **deepagents 0.7.5** (langchain 1.3 · langgraph 1.2), 2026-08-11

## 1. 결론 요약

| 계약 | 판정 | 근거 |
|---|---|---|
| ① 주입 (모델 교체) | ✅ 통과 | `create_deep_agent(model=...)`이 임의 `BaseChatModel` 인스턴스를 받음. API 키 없이 가짜 모델로 전 사이클 오프라인 실행 확인 |
| ② 결정론 재현 | ✅ 통과 | 같은 대본 모델 + T0-3 가짜 툴 → 2회 실행 결과(최종 메시지·툴 호출 시퀀스·발행 결과) 완전 일치 |
| ③ 착지점 통제 | ✅ 통과 (조건 이해 필요, §3) | 실세계 부작용 경로 = 우리가 `tools=`로 넘긴 툴뿐. 내장 파일 툴은 state 내 **가상** 파일시스템 |

**권고: 폴백(자작 오케스트레이터) 불필요 — deepagents 채택.**
단, 기획서상 이 결정은 **공동 결정** 사항이므로 팀 확인 후 확정한다.

## 2. 검증 방법

가짜 모델(`ScriptedChatModel`) + T0-3 가짜 툴(`FakePublish`)로 "발행 사이클 1회"를 API 키 없이 실행:

1. 모델 대본: ① `publish_tool(caption, idempotency_key)` 호출 → ② "발행 완료" 응답.
2. `create_deep_agent(model=대본모델, tools=[publish_tool])` → `invoke()`.
3. 검증: 오프라인 완주(①) / 2회 실행 동일 결과(②) / 발행이 툴 계약을 통해서만 발생 + 가상 FS 무변화(③).

## 3. 발견사항 (구현 시 알아야 할 것)

1. **가짜 모델은 `bind_tools` 오버라이드 필요**: langchain 내장 `GenericFakeChatModel`은 `bind_tools` 미구현(NotImplementedError)인데 create_agent가 항상 호출함 → no-op 오버라이드 서브클래스로 해결(스파이크 코드 참조). FR-C3 결정론 테스트의 표준 패턴이 될 것.
2. **내장 툴이 기본 포함됨**: `ls`·`read_file`·`write_file`·`edit_file`·`glob`·`grep`·`execute`·`task`(서브에이전트 호출). 단:
   - 파일 툴은 기본 `StateBackend` = **그래프 state 안의 가상 파일시스템**. 실제 디스크·DB에 닿지 않음 → 착지점 위반 아님.
   - `execute`는 샌드박스 백엔드가 없으면 에러 문자열만 반환(실행 불가).
   - 내장 툴 제거가 필요하면 `HarnessProfile.excluded_tools`로 가능.
3. **착지점 통제의 구조**: DB 3착지점(`content_item.body`·`playbook.guidance`·`analysis_note.body`)은 T0-3 툴 계약(`write_playbook` 등)을 통해서만 노출한다. deepagents는 넘긴 툴 외 부작용 수단을 LLM에 주지 않으므로 FR-C4의 "착지점 외 기록 경로 부재"가 구조적으로 성립.
4. **버전 리스크**: pre-1.0(0.7.5)이라 deprecation 진행 중(`model=None` 기본모델 등 — 우리는 항상 명시 주입이므로 무관). 의존성 56개(langchain·langgraph 스택) 추가됨. `uv.lock`으로 고정.
5. **텔레메트리**: langsmith가 딸려 오지만 추적은 env 옵트인(`LANGSMITH_TRACING`) — 기본 꺼짐, 외부 전송 없음.

## 4. 다음 단계

- 팀 공동 결정: 폴백 여부 확정 → [13-로드맵-리스크](../plan/13-로드맵-리스크.md) Phase 0 ① 게이트 통과 기록.
- C2(에이전트 프롬프트)·C5(러너)에서 이 스파이크의 주입 패턴(`ScriptedChatModel` + 가짜 툴)을 결정론 테스트 표준으로 재사용.
