"""트렌드 소스 fetcher 5종 — 순수 파서(픽스처 바이트) + opener 주입(네트워크 없음).

google_trends와 같은 규율: 파싱은 순수 함수라 픽스처로, 네트워크는 얇은 fetch에
격리하고 가짜 opener를 주입한다. 실패는 예외로 전파(서비스가 격리).
"""

import json
import urllib.request
from typing import Any

import pytest

from sns.research.sources.github_trending import fetch_github_trending, parse_github_trending
from sns.research.sources.llm_grounding import fetch_llm_grounding, parse_llm_grounding
from sns.research.sources.naver_datalab import (
    _judge_trend,
    fetch_naver_datalab,
    parse_naver_datalab,
)
from sns.research.sources.naver_search import fetch_naver_search, parse_naver_search
from sns.research.sources.youtube_popular import fetch_youtube_popular, parse_youtube_popular


class _FakeResponse:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self.read_amt: int | None = None

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self, amt: int = -1) -> bytes:
        self.read_amt = amt
        return self._data


def _opener(data: bytes, sink: dict[str, Any] | None = None):
    def opener(target: Any, timeout: float) -> _FakeResponse:
        if sink is not None:
            sink["target"] = target
            sink["timeout"] = timeout
        return _FakeResponse(data)

    return opener


# ── 네이버 검색 ──────────────────────────────────────────────────────────
_NAVER_NEWS = json.dumps(
    {"items": [{"title": "새 <b>프레임워크</b> 출시"}, {"title": "AI &amp; 개발"}, {"title": "  "}]}
).encode()


def test_naver_search_parse_strips_tags_and_entities() -> None:
    assert parse_naver_search(_NAVER_NEWS) == ("새 프레임워크 출시", "AI & 개발")


def test_naver_search_fetch_sends_auth_and_query() -> None:
    sink: dict[str, Any] = {}
    out = fetch_naver_search(
        1, client_id="cid", client_secret="sec", query="파이썬", opener=_opener(_NAVER_NEWS, sink)
    )
    assert out == ("새 프레임워크 출시",)  # limit=1
    req = sink["target"]
    assert isinstance(req, urllib.request.Request)
    assert req.get_header("X-naver-client-id") == "cid"
    assert req.get_header("X-naver-client-secret") == "sec"
    assert "query=%ED%8C%8C%EC%9D%B4%EC%8D%AC" in req.full_url  # '파이썬' urlencoded


# ── 네이버 데이터랩 ──────────────────────────────────────────────────────
_DATALAB = json.dumps(
    {
        "results": [
            {"title": "개발자", "data": [{"ratio": 40.0}, {"ratio": 60.0}]},
            {"title": "파이썬", "data": [{"ratio": 50.0}, {"ratio": 30.0}]},
            {"title": "AI", "data": [{"ratio": 50.0}]},  # 점 1개 → 판정 불가로 제외
        ]
    }
).encode()


@pytest.mark.parametrize(
    ("first", "last", "expected"),
    [
        (40.0, 60.0, "상승"),
        (50.0, 30.0, "하락"),
        (50.0, 52.0, "유지"),
        (0.0, 0.0, "유지"),
        (0.0, 5.0, "상승"),
    ],
)
def test_datalab_judge_trend(first: float, last: float, expected: str) -> None:
    assert _judge_trend(first, last) == expected


def test_datalab_parse_judges_and_skips_short() -> None:
    assert parse_naver_datalab(_DATALAB) == ("개발자: 상승", "파이썬: 하락")


def test_datalab_fetch_posts_json_body() -> None:
    sink: dict[str, Any] = {}
    out = fetch_naver_datalab(
        10, client_id="cid", client_secret="sec", opener=_opener(_DATALAB, sink)
    )
    assert out == ("개발자: 상승", "파이썬: 하락")
    req = sink["target"]
    assert req.method == "POST"
    body = json.loads(req.data)
    assert body["timeUnit"] == "date"
    assert [g["groupName"] for g in body["keywordGroups"]] == ["개발자", "파이썬", "AI"]


# ── YouTube 인기영상 ─────────────────────────────────────────────────────
_YOUTUBE = json.dumps(
    {"items": [{"snippet": {"title": "쇼츠 튜토리얼"}}, {"snippet": {"title": "릴스 팁"}}]}
).encode()


def test_youtube_parse_titles() -> None:
    assert parse_youtube_popular(_YOUTUBE) == ("쇼츠 튜토리얼", "릴스 팁")


def test_youtube_fetch_includes_key_and_region() -> None:
    sink: dict[str, Any] = {}
    out = fetch_youtube_popular(5, api_key="k", opener=_opener(_YOUTUBE, sink))
    assert out == ("쇼츠 튜토리얼", "릴스 팁")
    assert "key=k" in sink["target"]
    assert "regionCode=KR" in sink["target"]


# ── GitHub 트렌딩 ────────────────────────────────────────────────────────
_GITHUB = (
    b'<article><h2 class="h3"><a href="/openai/whisper">openai / whisper</a></h2></article>'
    b'<article><h2 class="h3"><a href="/torvalds/linux">t</a></h2></article>'
    b'<article><h2 class="h3"><a href="/openai/whisper">dup</a></h2></article>'
)


def test_github_parse_dedupes_in_order() -> None:
    assert parse_github_trending(_GITHUB) == ("openai/whisper", "torvalds/linux")


def test_github_fetch_caps_read_and_limits() -> None:
    resp = _FakeResponse(_GITHUB)
    out = fetch_github_trending(1, opener=lambda target, timeout: resp)
    assert out == ("openai/whisper",)
    assert resp.read_amt == 5_000_000


# ── LLM 그라운딩 ─────────────────────────────────────────────────────────
_GEMINI = json.dumps(
    {"candidates": [{"content": {"parts": [{"text": "- 주제A\n* 주제B\n\n  \n- 주제C"}]}}]}
).encode()


def test_llm_parse_extracts_bulleted_lines() -> None:
    assert parse_llm_grounding(_GEMINI) == ("주제A", "주제B", "주제C")


def test_llm_parse_empty_candidates() -> None:
    assert parse_llm_grounding(json.dumps({"candidates": []}).encode()) == ()


def test_llm_fetch_posts_with_key() -> None:
    sink: dict[str, Any] = {}
    out = fetch_llm_grounding(2, api_key="gk", opener=_opener(_GEMINI, sink))
    assert out == ("주제A", "주제B")
    req = sink["target"]
    assert req.method == "POST"
    assert "key=gk" in req.full_url
    assert json.loads(req.data)["tools"] == [{"google_search": {}}]


def test_llm_fetch_binds_channel_topic_into_prompt() -> None:
    """채널 프로필 최소 바인딩 — topic이 있으면 그 분야로 묻는다(기본은 기존 개발 프롬프트)."""
    sink: dict[str, Any] = {}
    fetch_llm_grounding(2, api_key="gk", topic="요리 베이킹", opener=_opener(_GEMINI, sink))
    prompt = json.loads(sink["target"].data)["contents"][0]["parts"][0]["text"]
    assert "요리 베이킹" in prompt and "개발자" not in prompt

    sink2: dict[str, Any] = {}
    fetch_llm_grounding(2, api_key="gk", opener=_opener(_GEMINI, sink2))
    default_prompt = json.loads(sink2["target"].data)["contents"][0]["parts"][0]["text"]
    assert "개발자" in default_prompt  # 미지정이면 기존 동작 그대로


def test_research_topic_returns_grounded_note() -> None:
    """주제 리서치 — 근거 노트 원문을 그대로 돌려준다(줄 파싱 없음)."""
    from sns.agents.research import research_topic

    note = json.dumps(
        {"candidates": [{"content": {"parts": [{"text": "- 오븐 예열은 180도 (출처: a.com)"}]}}]}
    ).encode()
    sink: dict[str, Any] = {}
    out = research_topic("베이킹 예열", api_key="gk", opener=_opener(note, sink))
    assert out == "- 오븐 예열은 180도 (출처: a.com)"
    prompt = json.loads(sink["target"].data)["contents"][0]["parts"][0]["text"]
    assert "베이킹 예열" in prompt
    assert json.loads(sink["target"].data)["tools"] == [{"google_search": {}}]


def test_llm_grounding_url_follows_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """gemini-2.0-flash 하드코딩이 모델 은퇴로 404를 내던 실사고 — 모델은 env를 따른다."""
    from sns.research.sources.llm_grounding import gemini_url

    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert "gemini-3.5-flash:generateContent" in gemini_url()
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    assert "gemini-3.5-flash-lite:generateContent" in gemini_url()

    sink: dict[str, Any] = {}
    fetch_llm_grounding(1, api_key="gk", opener=_opener(_GEMINI, sink))
    assert "gemini-3.5-flash-lite:generateContent" in sink["target"].full_url
