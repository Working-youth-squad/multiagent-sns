"""온보딩 사전 인터뷰 — 계정의 주제·컨셉·목표·캐릭터를 정하는 위저드의 도메인 계층.

- [sns.onboarding.profile]: 인터뷰 산출물(ChannelProfile)의 정본 파서·검증.
- [sns.onboarding.store]: 채널·프로필 영속화 계약(InMemory/Pg).
- 웹 화면은 [sns.web.onboarding], 실행은 scripts/run_onboarding_web.py.
"""
