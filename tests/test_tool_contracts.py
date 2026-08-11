import pytest

from sns.tools.contracts import (
    MetricValue,
    PollMetrics,
    Publish,
    ReadStats,
    RenderMedia,
    ResearchTrends,
    ToolError,
    TopicStat,
    WritePlaybook,
)
from sns.tools.fakes import (
    FakePollMetrics,
    FakePublish,
    FakeReadStats,
    FakeRenderMedia,
    FakeResearchTrends,
    FakeWritePlaybook,
)


def test_fakes_satisfy_contracts() -> None:
    # Protocol 구조적 타이핑 확인 — 계약 시그니처가 어긋나면 여기서 깨진다
    research: ResearchTrends = FakeResearchTrends()
    render: RenderMedia = FakeRenderMedia()
    publish: Publish = FakePublish()
    poll: PollMetrics = FakePollMetrics()
    stats: ReadStats = FakeReadStats()
    playbook: WritePlaybook = FakeWritePlaybook()
    assert all([research, render, publish, poll, stats, playbook])


def test_research_trends_isolates_failing_source() -> None:
    research = FakeResearchTrends(failing_sources={"naver_search"})
    digest = research()
    by_source = {r.source: r for r in digest.source_results}
    assert by_source["naver_search"].ok is False
    assert by_source["naver_search"].items == ()
    # 한 소스가 죽어도 다이제스트는 나온다 (FR-G4)
    assert "google_trends-topic-1" in digest.digest_markdown


def test_render_media_is_deterministic() -> None:
    render = FakeRenderMedia()
    spec = {"template": "card", "title": "훅"}
    a, b = render(spec, "image"), render(spec, "image")
    assert a.checksum == b.checksum  # FR-M1
    assert a != render({"template": "card", "title": "다른 훅"}, "image")


def test_publish_is_idempotent() -> None:
    publish = FakePublish()
    media = FakeRenderMedia()({"t": 1}, "video")
    first = publish("instagram", media, "캡션", idempotency_key="pub-1")
    second = publish("instagram", media, "캡션", idempotency_key="pub-1")
    assert first == second  # 같은 키 재호출 = 같은 결과, 이중 발행 0 (FR-P3)
    assert first.post_id is not None
    assert publish.calls == ["pub-1", "pub-1"]


def test_publish_error_carries_classification() -> None:
    err = ToolError(error_class="quota", error_raw="rate limit exceeded")
    publish = FakePublish(error=err)
    result = publish("youtube", FakeRenderMedia()({}, "video"), "c", idempotency_key="k")
    assert result.post_id is None
    assert result.error == err  # FR-P4: 분류 + 원문 보존


def test_poll_metrics_missing_is_null() -> None:
    poll = FakePollMetrics(missing_keys={"skip_rate"})
    values = {v.metric_key: v for v in poll("instagram", "post-1", 0)}
    assert values["skip_rate"].missing is True
    assert values["skip_rate"].value is None  # 결측=NULL, 0으로 안 채움 (NFR-3)
    assert values["reach"].missing is False
    assert poll("instagram", "post-1", 0) == poll("instagram", "post-1", 0)  # 결정론


def test_metric_value_rejects_missing_xor_violation() -> None:
    with pytest.raises(ValueError):
        MetricValue(metric_key="reach", value=1.0, missing=True)
    with pytest.raises(ValueError):
        MetricValue(metric_key="reach", value=None, missing=False)


def test_read_stats_filters_by_platform() -> None:
    rows = (
        TopicStat("t1", "reels", "instagram", trials=3, reward_sum=1.5),
        TopicStat("t1", "shorts", "youtube", trials=2, reward_sum=0.7),
    )
    stats = FakeReadStats(rows)
    assert stats() == rows
    assert stats(platform="youtube") == (rows[1],)


def test_write_playbook_advances_version_per_scope() -> None:
    playbook = FakeWritePlaybook()
    assert playbook("global", "지침 1").version == 1
    assert playbook("global", "지침 2").version == 2
    assert playbook("platform", "IG 지침", scope_ref="instagram").version == 1
