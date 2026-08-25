"""온보딩 인터뷰 **프리뷰** — DB·LLM·유료 API 없이 눈으로 확인용.

uv run python scripts/preview_onboarding_web.py   → http://127.0.0.1:8002/

- 저장소: InMemoryOnboardingStore (서버 끄면 사라짐 — 실 운영은 run_onboarding_web.py)
- 추천: 고정 샘플(트렌드·LLM 미호출) — 화면 6의 추천 카드 모양 확인용
- 캐릭터: 생성 대신 자리표시 URL 박제(유료 API 미호출)
"""

import sys
from dataclasses import replace

import uvicorn

from sns.onboarding.profile import ChannelProfile
from sns.onboarding.store import InMemoryOnboardingStore
from sns.web.onboarding.app import create_app


def sample_recommend(profile: ChannelProfile) -> dict[str, object]:
    return {
        "direction": (
            f"'{profile.topic_major}' 채널은 지금 {', '.join(profile.topic_subs)} 쪽 "
            "관심이 높아요. 초보 눈높이의 짧은 실전 팁 위주로 시작하는 걸 추천해요. "
            "(프리뷰 — 실제 서비스에선 실시간 트렌드 + LLM이 생성)"
        ),
        "focus_subs": list(profile.topic_subs[:2]),
        "hot_trends": ["샘플 트렌드 A — 실시간 수집 항목이 여기 표시됩니다", "샘플 트렌드 B"],
        "name_ideas": [
            f"오늘의 {profile.topic_subs[0]}",
            f"{profile.topic_major}한입",
            f"매일 {profile.topic_major} 루틴",
        ],
        "tune_ideas": [
            "초보자도 따라할 수 있게 단계별로 풀어줘",
            f"{profile.topic_subs[0]} 비중을 더 높여줘",
            "말투를 더 친근하게 바꿔줘",
        ],
    }


def sample_character(profile: ChannelProfile) -> ChannelProfile:
    return replace(
        profile,
        character_image_url=f"(프리뷰) {profile.character_style} 캐릭터 — 실 서비스에서 생성",
        character_checksum="preview",
    )


def main() -> int:
    app = create_app(
        InMemoryOnboardingStore(),
        recommend_fn=sample_recommend,
        ensure_character_fn=sample_character,
        # refine_fn 미주입 → 줄글은 note로 보존되는 폴백 경로(그대로 확인 가능)
    )
    print("온보딩 인터뷰 프리뷰: http://127.0.0.1:8002/")
    uvicorn.run(app, host="127.0.0.1", port=8002)
    return 0


if __name__ == "__main__":
    sys.exit(main())
