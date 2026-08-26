"""PgMetricStore 통합 검증 — 라이브 PostgreSQL (M6, FR-L1~L4).

InMemory와 같은 행동을 SQL로도 지키는지 본다(test_metric_store와 짝). 추가로 여기서만
볼 수 있는 것 둘: **DB CHECK가 최종 관문인가**(결측=NULL, NFR-3), 그리고 **topic_stats의
(topic, format, platform)을 원장 조인으로 옳게 찾아오는가** — 호출부가 셋을 들고 다니지
않기로 한 결정(stores 모듈 docstring)이 실제로 성립하는 자리다.
"""

import psycopg
import pytest

from sns.learning.stores import PgMetricStore
from sns.tools.contracts import MetricValue
from tests.conftest import SeedFn


def _publish(
    db: psycopg.Connection, pub_id: str, *, post_id: str = "post-1", hours_ago: int = 0
) -> str:
    """시드된 대기 건을 발행 완료로 올린다 — 폴링 대상이 되는 최소 조건."""
    db.execute(
        """
        UPDATE publication
           SET status = 'published',
               external_post_id = %s,
               published_at = now() - make_interval(hours => %s)
         WHERE id = %s
        """,
        (post_id, hours_ago, pub_id),
    )
    return pub_id


def _sibling_publication(db: psycopg.Connection, pub_id: str) -> str:
    """같은 topic·format·채널로 발행 건을 하나 더 만든다.

    `seed`는 호출마다 topic을 새로 만들기 때문에, 누적(한 행에 trials 2)을 보려면
    원장을 직접 갈라 붙여야 한다 — 이 누적이 밴딧 표본의 정의다.
    """
    row = db.execute(
        """
        WITH src AS (
            SELECT ci.cycle_id, ci.topic_id, ci.format, ci.body, p.channel_id
              FROM publication p JOIN content_item ci ON ci.id = p.content_item_id
             WHERE p.id = %s
        ), ci2 AS (
            INSERT INTO content_item (cycle_id, topic_id, format, body, status)
            SELECT cycle_id, topic_id, format, body, 'approved' FROM src
            RETURNING id
        )
        INSERT INTO publication (content_item_id, channel_id)
        SELECT ci2.id, src.channel_id FROM ci2, src
        RETURNING id::text
        """,
        (pub_id,),
    ).fetchone()
    assert row is not None
    return str(row[0])


def _stats(db: psycopg.Connection) -> list[tuple]:
    return db.execute(
        "SELECT topic_id::text, format, platform, trials, reward_sum FROM topic_stats"
    ).fetchall()


# ── published_items ─────────────────────────────────────────────────


def test_pending_publication_is_not_a_polling_target(db: psycopg.Connection, seed: SeedFn) -> None:
    seed()  # status='pending' — 아직 세상에 없다.
    assert PgMetricStore(db).published_items() == ()


def test_published_item_carries_channel_mode_and_format(
    db: psycopg.Connection, seed: SeedFn
) -> None:
    """auto vs hybrid 비교(FR-E4)의 축이 건마다 따라와야 인사이트 탭이 갈라 셀 수 있다."""
    pub_id = _publish(db, seed(platform="instagram", fmt="reels", mode="hybrid"), post_id="ig-9")
    item = PgMetricStore(db).published_items()[0]
    assert item.publication_id == pub_id
    assert (item.platform, item.content_format, item.channel_mode) == (
        "instagram",
        "reels",
        "hybrid",
    )
    assert item.external_post_id == "ig-9"
    assert item.observed_windows == ()


def test_published_items_are_ordered_and_report_observed_windows(
    db: psycopg.Connection, seed: SeedFn
) -> None:
    old = _publish(db, seed(), post_id="p-old", hours_ago=50)
    new = _publish(db, seed(), post_id="p-new", hours_ago=2)
    store = PgMetricStore(db)
    store.save_observation(
        publication_id=old, window_index=0, values=[MetricValue("views", 5.0, False)]
    )
    items = store.published_items()
    assert [i.publication_id for i in items] == [old, new]
    assert items[0].observed_windows == (0,)


def test_since_excludes_older_publications(db: psycopg.Connection, seed: SeedFn) -> None:
    _publish(db, seed(), post_id="p-old", hours_ago=100)
    recent = _publish(db, seed(), post_id="p-new", hours_ago=1)
    row = db.execute("SELECT now() - interval '24 hours'").fetchone()
    assert row is not None
    picked = PgMetricStore(db).published_items(since=row[0])
    assert [i.publication_id for i in picked] == [recent]


# ── 관측 적재 ───────────────────────────────────────────────────────


def test_observation_roundtrip_keeps_missing_as_null(db: psycopg.Connection, seed: SeedFn) -> None:
    pub_id = _publish(db, seed())
    store = PgMetricStore(db)
    store.save_observation(
        publication_id=pub_id,
        window_index=0,
        values=[MetricValue("views", 12.0, False), MetricValue("avg_view_pct", None, True)],
    )
    values = {
        v.metric_key: v for v in store.read_observation(publication_id=pub_id, window_index=0)
    }
    assert values["views"] == MetricValue("views", 12.0, False)
    assert values["avg_view_pct"] == MetricValue("avg_view_pct", None, True)


def test_repeat_poll_of_same_window_is_absorbed(db: psycopg.Connection, seed: SeedFn) -> None:
    pub_id = _publish(db, seed())
    store = PgMetricStore(db)
    first = store.save_observation(
        publication_id=pub_id, window_index=1, values=[MetricValue("views", 10.0, False)]
    )
    again = store.save_observation(
        publication_id=pub_id, window_index=1, values=[MetricValue("views", 999.0, False)]
    )
    assert first is not None and again is None
    assert store.read_observation(publication_id=pub_id, window_index=1) == (
        MetricValue("views", 10.0, False),
    )
    count = db.execute(
        "SELECT count(*) FROM metric_observation WHERE publication_id = %s", (pub_id,)
    ).fetchone()
    assert count is not None and count[0] == 1


def test_db_check_rejects_zero_filled_missing(db: psycopg.Connection, seed: SeedFn) -> None:
    """계약(MetricValue)이 아니라 **DB**가 최종 관문임을 못박는다 — 우회 경로가 생겨도 막힌다."""
    pub_id = _publish(db, seed())
    PgMetricStore(db).save_observation(
        publication_id=pub_id, window_index=0, values=[MetricValue("views", 1.0, False)]
    )
    row = db.execute(
        "SELECT id FROM metric_observation WHERE publication_id = %s", (pub_id,)
    ).fetchone()
    assert row is not None
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute(
            "INSERT INTO metric_value (observation_id, metric_key, value, missing) "
            "VALUES (%s, 'reach', 0, true)",
            (row[0],),
        )


# ── reward · topic_stats ────────────────────────────────────────────


def test_reward_joins_topic_format_platform_from_the_ledger(
    db: psycopg.Connection, seed: SeedFn
) -> None:
    pub_id = _publish(db, seed(platform="youtube", fmt="shorts"))
    PgMetricStore(db).save_reward(publication_id=pub_id, reward_value=0.5, formula_version="v1")
    rows = _stats(db)
    assert len(rows) == 1
    _, fmt, platform, trials, reward_sum = rows[0]
    assert (fmt, platform, trials, reward_sum) == ("shorts", "youtube", 1, 0.5)


def test_recompute_does_not_double_count(db: psycopg.Connection, seed: SeedFn) -> None:
    pub_id = _publish(db, seed())
    store = PgMetricStore(db)
    store.save_reward(publication_id=pub_id, reward_value=0.5, formula_version="v1")
    store.save_reward(publication_id=pub_id, reward_value=0.9, formula_version="v2")
    assert [(r[3], r[4]) for r in _stats(db)] == [(1, 0.9)]
    assert store.read_reward(pub_id) == (0.9, "v2")


def test_null_reward_is_stored_but_not_counted(db: psycopg.Connection, seed: SeedFn) -> None:
    pub_id = _publish(db, seed())
    store = PgMetricStore(db)
    store.save_reward(publication_id=pub_id, reward_value=None, formula_version="v1")
    assert _stats(db) == []
    assert store.read_reward(pub_id) == (None, "v1")  # 미계산(None)과 다르다.


def test_two_publications_of_one_topic_accumulate(db: psycopg.Connection, seed: SeedFn) -> None:
    """같은 주제·포맷·플랫폼은 **한 행에** 쌓인다 — 밴딧이 읽는 단위(FR-L3)."""
    first = _publish(db, seed(), post_id="p-1")
    second = _publish(db, _sibling_publication(db, first), post_id="p-2")
    store = PgMetricStore(db)
    store.save_reward(publication_id=first, reward_value=0.4, formula_version="v1")
    store.save_reward(publication_id=second, reward_value=0.6, formula_version="v1")
    rows = _stats(db)
    assert len(rows) == 1
    assert (rows[0][3], round(rows[0][4], 6)) == (2, 1.0)


def test_separate_topics_get_separate_rows(db: psycopg.Connection, seed: SeedFn) -> None:
    first = _publish(db, seed(), post_id="p-1")
    second = _publish(db, seed(), post_id="p-2")  # 시드마다 topic이 새로 생긴다.
    store = PgMetricStore(db)
    store.save_reward(publication_id=first, reward_value=0.4, formula_version="v1")
    store.save_reward(publication_id=second, reward_value=0.6, formula_version="v1")
    assert sorted(s.reward_sum for s in store.read_topic_stats()) == [0.4, 0.6]
    assert {s.trials for s in store.read_topic_stats()} == {1}


def test_read_topic_stats_filters_by_platform(db: psycopg.Connection, seed: SeedFn) -> None:
    pub_id = _publish(db, seed(platform="youtube"))
    store = PgMetricStore(db)
    store.save_reward(publication_id=pub_id, reward_value=0.4, formula_version="v1")
    assert len(store.read_topic_stats(platform="youtube")) == 1
    assert store.read_topic_stats(platform="instagram") == ()


def test_reward_for_unknown_publication_is_rejected(db: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        PgMetricStore(db).save_reward(
            publication_id="00000000-0000-0000-0000-000000000000",
            reward_value=0.1,
            formula_version="v1",
        )


# ── playbook · analysis_note · event ────────────────────────────────


def test_playbook_versions_increment_per_scope(db: psycopg.Connection) -> None:
    store = PgMetricStore(db)
    # scope_ref가 NULL이어도 버전이 겹치지 않아야 한다(UNIQUE가 NULL을 못 막는 자리).
    assert store.save_playbook("global", "첫 지침").version == 1
    assert store.save_playbook("global", "둘째 지침").version == 2
    assert store.save_playbook("platform", "유튜브 지침", "youtube").version == 1
    rows = db.execute("SELECT count(*) FROM playbook").fetchone()
    assert rows is not None and rows[0] == 3


def test_analysis_note_records_flag(db: psycopg.Connection) -> None:
    note_id = PgMetricStore(db).save_analysis_note(
        cycle_id=None, body="근거 부족 — 표본 5건", insufficient_evidence=True
    )
    row = db.execute(
        "SELECT body, insufficient_evidence FROM analysis_note WHERE id = %s", (note_id,)
    ).fetchone()
    assert row is not None and row[1] is True


def test_metric_polled_event_is_accepted_by_the_kind_check(db: psycopg.Connection) -> None:
    """폴링이 돌았다는 사실은 전 지표가 결측이어도 남아야 한다 — 미실행과 구분된다."""
    PgMetricStore(db).log_event(
        cycle_id=None, kind="metric_polled", payload={"window_index": 0, "missing_all": True}
    )
    row = db.execute("SELECT kind, payload FROM run_event").fetchone()
    assert row is not None
    assert row[0] == "metric_polled"
    assert row[1]["window_index"] == 0
