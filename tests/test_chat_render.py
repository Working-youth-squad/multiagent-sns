"""챗봇 화면 렌더 — CLI가 지키는 표시 규율 5개가 화면에서도 지켜지는가.

이 파일이 지키려는 것은 `scripts/rank_keywords.py:render()`와 같다. 규율이 깨지는
양식이 전부 "사실 두 개를 하나로 뭉개기"라, 각 테스트는 **뭉개진 표현이 나오지
않는지**를 함께 확인한다.
"""

from sns.chat.store import ChatMessage, Conversation
from sns.research.keywords import aggregate, ranking_to_dict
from sns.tools.contracts import SourceResult
from sns.web.chat.render import render_conversation, render_index, render_ranking
from tests.test_chat_app import _now


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "query": "개발자",
        "filter_mode": "active",
        "band": [0.1, 0.4],
        "reason": "rank_std 0.1000~0.4000",
        "sources_ok": ["naver_autocomplete", "google_suggest", "youtube_suggest"],
        "sources_failed": [],
        "candidates": [],
        "pool": [],
        "dropped": [],
        "below_min_present": [],
        "unscored": [],
        "excluded": [],
    }
    base.update(overrides)
    return base


def _stat(text: str, *, present: int = 3, mean: float = 0.3, std: float | None = 0.2) -> dict:
    return {
        "text": text,
        "variants": [text],
        "present_count": present,
        "observed_mean": mean,
        "rank_std": std,
        "scored": std is not None,
        "per_source": {},
    }


# ── 규율 1: rank_std=None을 0.0으로 표시하지 않는다 ────────────────────


def test_undefined_rank_std_is_not_rendered_as_zero() -> None:
    html = render_ranking(
        _payload(candidates=[_stat("개발자 연봉", present=1, std=None)], unscored=["개발자 연봉"])
    )
    assert "미정의" in html
    # "0.0000"이 나오면 "불일치가 없다"로 읽힌다 — 잴 수 없었다는 사실과 정반대다.
    assert "0.0000" not in html


def test_exact_zero_rank_std_is_still_a_number() -> None:
    """정확히 0.0인 완전 합의는 '미정의'가 아니다 — 두 사실을 뒤집지 않는지 확인."""
    html = render_ranking(_payload(candidates=[_stat("개발자 연봉", std=0.0)]))
    assert "0.0000" in html
    assert "미정의" not in html


# ── 규율 2: filter_mode 3값을 뭉개지 않는다 ────────────────────────────


def test_passthrough_does_not_claim_filtering_happened() -> None:
    html = render_ranking(
        _payload(filter_mode="passthrough", candidates=[_stat("개발자 연봉")], band=None)
    )
    assert "필터가 열리지 않았습니다" in html
    # 이 문장이 나오면 걸러지지 않은 목록을 걸러낸 것처럼 보여주는 것이다.
    assert "걸러낸 결과" not in html


def test_three_filter_modes_render_distinct_sentences() -> None:
    seen = {
        mode: render_ranking(_payload(filter_mode=mode, candidates=[_stat("개발자 연봉")]))
        for mode in ("active", "passthrough", "off")
    }
    assert "걸러낸 결과" in seen["active"]
    assert "필터가 열리지 않았습니다" in seen["passthrough"]
    assert "필터를 끈 전체 목록" in seen["off"]


def test_unknown_filter_mode_is_surfaced_not_folded() -> None:
    """모르는 값을 조용히 'off'로 접으면 그것도 뭉개기다."""
    html = render_ranking(_payload(filter_mode="언젠가_생길_값"))
    assert "알 수 없는 필터 상태" in html
    assert "필터를 끈 전체 목록" not in html


# ── 규율 3: unscored는 candidates의 부분집합 ───────────────────────────


def test_unscored_is_labeled_as_subset_not_extra_candidates() -> None:
    html = render_ranking(
        _payload(
            candidates=[_stat("개발자 연봉"), _stat("개발자 취업", present=1, std=None)],
            unscored=["개발자 취업"],
        )
    )
    assert "위 후보 2건 중 1건" in html
    assert "별도 후보가 아닙니다" in html


def test_unscored_count_never_exceeds_shown_candidates() -> None:
    """`unscored`는 `top` 컷 **이전** 전량에서 계산된다 — 그대로 세면 후보 수를 넘는다.

    실측(질의어 '개발자')에서 후보 10건에 미판정 23건이 나왔다. "후보 10건 중 23건"은
    규율 3이 막으려던 바로 그 모양이라, 표 안과 표 밖을 나눠 세야 한다.
    """
    candidates = [_stat(f"키워드{i}", present=1, std=None) for i in range(3)]
    html = render_ranking(
        _payload(
            candidates=candidates,
            unscored=[f"키워드{i}" for i in range(3)] + ["표밖1", "표밖2"],
        )
    )
    assert "위 후보 3건 중 3건" in html
    assert "위 후보 3건 중 5건" not in html
    # 잘려나간 미판정도 조용히 사라지지 않는다.
    assert "표에 없는 후보 중에도 2건" in html


def test_unscored_entirely_outside_the_cut_is_not_claimed_as_shown() -> None:
    html = render_ranking(_payload(candidates=[_stat("보이는 후보")], unscored=["표밖1", "표밖2"]))
    assert "위 후보" not in html  # 표 안에는 미판정이 없다
    assert "표에 없는 후보 중에도 2건" in html


# ── 규율 4: 소스 실패를 숨기지 않는다 ──────────────────────────────────


def test_failed_sources_are_shown() -> None:
    html = render_ranking(
        _payload(
            sources_ok=["google_suggest"],
            sources_failed=["naver_autocomplete", "youtube_suggest"],
            candidates=[_stat("개발자 연봉", present=1, std=None)],
        )
    )
    assert "naver_autocomplete" in html
    assert "youtube_suggest" in html
    assert "실패" in html


# ── 규율 5: 밴드 힌트는 filter_mode == "active" 일 때만 ────────────────


def test_empty_active_suggests_disabling_filter() -> None:
    html = render_ranking(_payload(filter_mode="active", candidates=[]))
    assert "필터를 끄면" in html


def test_empty_passthrough_does_not_blame_the_filter() -> None:
    html = render_ranking(_payload(filter_mode="passthrough", candidates=[], band=None))
    # 밴드가 자른 게 아닌데 끄라고 권하면 필터 탓으로 오인시킨다.
    assert "필터를 끄면" not in html
    assert "밴드는 열리지 않았습니다" in html


# ── 밴드 밖 목록: 정확히 0.0인 꼬리가 사라지지 않는다 ──────────────────


def test_dropped_zero_std_tail_is_listed() -> None:
    """`if s['rank_std']`로 거르면 0.0인 하위 꼬리가 통째로 사라진다 — None만 빼야 한다."""
    html = render_ranking(
        _payload(candidates=[_stat("개발자 면접")], dropped=[_stat("개발자 연봉", std=0.0)])
    )
    assert "개발자 연봉(0.000)" in html


# ── 실제 산출물과 모양이 같은가 ────────────────────────────────────────


def test_renders_real_ranking_output() -> None:
    """`ranking_to_dict` 산출을 그대로 먹는다 — DB 왕복분과 방금 만든 것이 같은 모양."""
    results = (
        SourceResult(source="naver_autocomplete", ok=True, items=("개발자 연봉", "개발자 취업")),
        SourceResult(source="google_suggest", ok=True, items=("개발자 취업", "개발자 연봉")),
        SourceResult(source="youtube_suggest", ok=False),
    )
    payload = ranking_to_dict(aggregate("개발자", results))
    html = render_ranking(payload)

    assert "개발자 연봉" in html and "개발자 취업" in html
    assert "youtube_suggest" in html  # 실패 소스 노출(규율 4)
    # 소스가 2곳뿐이라 밴드가 열리지 않는다 — 걸러낸 척하지 않는지.
    assert payload["filter_mode"] == "passthrough"
    assert "걸러낸 결과" not in html


# ── XSS ────────────────────────────────────────────────────────────────


def test_user_text_is_escaped() -> None:
    conversation = Conversation(
        conversation_id="c1", channel_id=None, title="<script>x</script>", created_at=_now()
    )
    messages = (
        ChatMessage(
            message_id="m1",
            role="user",
            body="<img src=x onerror=alert(1)>",
            payload=None,
            created_at=_now(),
        ),
    )
    html = render_conversation(conversation, messages)
    assert "<img src=x" not in html
    assert "&lt;img src=x" in html
    assert "<script>x</script>" not in html


def test_ranking_keyword_text_is_escaped() -> None:
    html = render_ranking(_payload(candidates=[_stat("<b>개발자</b>")]))
    assert "<b>개발자</b>" not in html
    assert "&lt;b&gt;개발자&lt;/b&gt;" in html


# ── 목록 화면 ──────────────────────────────────────────────────────────


def test_index_lists_conversations_and_offers_start() -> None:
    conversations = (
        Conversation(conversation_id="c1", channel_id=None, title="개발자", created_at=_now()),
    )
    html = render_index(conversations)
    assert 'action="/conversations"' in html
    assert "/c/c1" in html
    assert "개발자" in html


def test_index_empty_state() -> None:
    html = render_index(())
    assert "새 대화 시작" in html
