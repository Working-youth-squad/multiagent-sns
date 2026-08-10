# multiagent-sns

멀티에이전트(deepagents) 기반 **자율 SNS 성장 엔진**.

개발자 주제 콘텐츠를 **Instagram 피드 게시물 · Instagram 릴스 · YouTube 쇼츠**에
딥에이전트가 자동으로 기획·제작·발행하고, 반응(지표)을 학습해 채널을 키운다.

## 기획 문서

- 📘 [docs/PLAN.md](docs/PLAN.md) — 기획서(개요·아키텍처·멀티에이전트 구성·task 분할·로드맵)
- 📐 [docs/SPEC.md](docs/SPEC.md) — 상세 요구사항(FR/NFR)·예외/테스트 케이스·DoD·외부 제약
- 🗂️ [docs/ERD.md](docs/ERD.md) — 데이터 모델(ERD 초안)
- 📊 [docs/diagrams/](docs/diagrams/) — 아키텍처·발행 사이클·학습 루프·유저 플로우·ERD (Mermaid)

## 핵심 원칙

공식 API만 사용 · 결정론 재현 · 멱등 발행 · 지표 결측=NULL · 정직 귀인 · 자기 채널 성장만 측정.

> 상태: **기획(제안)** — 팀원이 task를 나누어 개발 착수할 수 있도록 작성. 그린필드(신규 구현).
