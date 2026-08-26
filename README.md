# multiagent-sns

멀티에이전트(deepagents) 기반 **자율 SNS 성장 엔진**.

개발자 주제 콘텐츠를 **Instagram 피드 게시물 · Instagram 릴스 · YouTube 쇼츠**에
딥에이전트가 자동으로 기획·제작·발행하고, 반응(지표)을 학습해 채널을 키운다.

## 기획 문서 (v2 — 기능별 세분화)

- 📘 [docs/PLAN.md](docs/PLAN.md) — **총괄**: 개요·배경·개발 원칙·성공 기준(DoD/S1~S5/kill criteria)·문서 목차
- 📂 [docs/plan/](docs/plan/) — 기능별 상세 13편: 실험설계 · 아키텍처-스택 · 멀티에이전트 · 트렌드조사 · 콘텐츠생성-품질게이트 · 미디어렌더 · 발행 · 지표-학습 · 알고리즘-신호-최적화 · 웹-알림 · 데이터모델 · 비기능-테스트 · 로드맵-리스크
- 📊 [docs/diagrams/](docs/diagrams/) — 아키텍처·발행 사이클·학습 루프·유저 플로우·ERD (Mermaid)
  — **기획서가 아니라 현재 코드를 그린다.** 계획만 있고 코드가 없는 항목은 점선/`미구현`으로 표시한다.

> 구 SPEC.md·ERD.md의 내용은 전부 docs/plan/ 하위 문서로 이관됨 (유실 없음).

## 로컬 개발

```bash
uv sync                        # 의존성 설치 (Python 3.12, https://docs.astral.sh/uv/)
cp .env.example .env           # 환경변수 — 키 발급처는 파일 주석 참조
docker compose up -d postgres  # 로컬 PostgreSQL
uv run pytest                  # 테스트
```

**시크릿**: 환경변수는 `.env`(gitignore), OAuth 클라이언트·토큰 같은 파일은 `.secrets/`(gitignore).
`.env`는 `scripts/` 진입점에서만 읽는다 — 라이브러리(`sns/`)는 환경변수 주입만 받는다.

**테스트 DB는 분리돼 있다**: `pytest`는 스키마를 DROP하고 테이블을 TRUNCATE하므로
`DATABASE_URL`의 DB를 그대로 쓰지 않고 이름에 `_test`를 붙인 별도 DB(`sns_test`)를 쓴다.
없으면 자동으로 만든다 — 환경변수를 따로 설정할 필요 없다. 개발 DB를 가리킨 채로 파괴적
작업을 하려 하면 `tests/dbguard.py`가 막는다(작업 중이던 사이클 원장이 날아가는 사고 방지).

**영상 렌더**에는 ffmpeg/ffprobe가 PATH에 있어야 한다(없으면 해당 테스트는 skip).

## 핵심 원칙

공식 API만 사용 · 결정론 재현 · 멱등 발행 · 지표 결측=NULL · 정직 귀인 · 자기 채널 성장만 측정.

> 상태: **기획(제안)** — 팀원이 task를 나누어 개발 착수할 수 있도록 작성. 그린필드(신규 구현).
