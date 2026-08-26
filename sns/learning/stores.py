"""지표·학습 영속화 seam — `MetricStore` 계약 + InMemory/Pg 구현 (M6, FR-L1~L5).

지금까지 `metric_observation`·`metric_value`·`reward`·`topic_stats`·`playbook`·
`analysis_note`는 **스키마만 있고 채우는 코드가 없었다**(001_initial.sql에 여섯 테이블이
있으나 INSERT는 레포 전체에 0건). 폴러(FR-L1)·RewardFn(FR-L2)·분석글(FR-L5)이 서로를
기다리지 않고 병렬로 붙을 수 있도록, 그 셋이 공유하는 **쓰기 지점만** 먼저 동결한다.

`CycleStore`(sns.runner.store)·`PublishAttemptStore`(sns.publish.stores)와 같은 규율:

- 순수 로직은 `InMemoryMetricStore`로 DB 없이 결정론 테스트한다(NFR-2).
- 운영 SQL은 `PgMetricStore`에만 있고, **autocommit 커넥션**을 주입받는다.
- 계약에 없는 되읽기를 호출부가 내부 dict에서 직접 뒤지지 않게, 필요한 조회는 계약에 둔다.

**정책은 여기 없다.** 어느 창을 언제 폴링할지(FR-L1 6·24·72h)는 순수 스케줄러가, reward
산식(FR-L2)은 RewardFn이 정한다. 이 모듈은 "무엇이 발행됐고 무엇이 이미 관측됐나"를
돌려주고, 주어진 값을 적재할 뿐이다 — 그래야 정책을 DB 없이 시험할 수 있다.

## 이 계약이 대신 지키는 불변식 두 가지

1. **결측=NULL** (NFR-3). `MetricValue.__post_init__`이 XOR을 이미 강제하지만, DB의
   `metric_missing_xor` CHECK가 최종 관문이다. 0으로 채우는 경로를 만들지 않는다.
2. **중복 집계 금지**. `save_reward`가 `reward`와 `topic_stats`를 **한 트랜잭션에서 함께**
   갱신하고, 이전 값과의 차분만 더한다. 호출부가 재계산으로 두 번 부르면 `trials`가 2로
   부풀고 밴딧(FR-L3)이 조용히 오염된다 — 그 계산을 호출부에 맡기지 않는 이유다.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

import psycopg
from psycopg.types.json import Json

from sns.tools.contracts import (
    ContentFormat,
    MetricValue,
    Platform,
    PlaybookScope,
    PlaybookVersion,
    ReadStats,
    TopicStat,
    WritePlaybook,
)

# run_event.kind CHECK(001_initial.sql)의 부분집합 — 학습 루프가 쓰는 값만.
# 'metric_polled'는 이 루프 전용이다: 폴링이 돌았다는 사실은 값이 전부 결측이어도
# 남아야 한다(폴러가 안 돈 것과 API가 값을 안 준 것은 다른 사건이다).
LearningEventKind = Literal["metric_polled", "notice", "error"]

# 채널 운영 모드 — auto vs hybrid 비교(FR-E4)의 축이라 발행 건마다 따라다녀야 한다.
ChannelMode = Literal["auto", "hybrid", "off"]


@dataclass(frozen=True)
class PublishedItem:
    """폴링·보상 계산의 대상 1건 — 발행 원장에서 필요한 것만 평평하게 편 뷰.

    `observed_windows`가 함께 오는 이유: "다음에 무엇을 폴링할까"는 발행 시각과 이미
    관측한 창의 함수다. 둘을 따로 조회하면 호출부가 N+1을 돌거나, 더 나쁘게는 이미
    적재된 창을 다시 불러 API 쿼터를 태운다.
    """

    publication_id: str
    platform: Platform
    external_post_id: str
    published_at: datetime
    content_format: ContentFormat
    topic_id: str
    channel_mode: ChannelMode
    observed_windows: tuple[int, ...] = ()


class MetricStore(Protocol):
    """폴러·RewardFn·Analyst가 의존하는 유일한 영속화 계약. 모든 id는 문자열."""

    def published_items(
        self, *, since: datetime | None = None, limit: int = 200
    ) -> tuple[PublishedItem, ...]:
        """발행 완료(`status='published'` + post_id + 발행시각) 건을 발행순으로.

        `since`는 폴링 지평(FR-L1의 '이후 일 1회'를 영원히 돌리지 않기 위한 하한)이다.
        """
        ...

    def save_observation(
        self,
        *,
        publication_id: str,
        window_index: int,
        values: Sequence[MetricValue],
        observed_at: datetime | None = None,
    ) -> str | None:
        """관측 1건 적재. 이미 있는 창이면 **아무것도 하지 않고 None**(멱등).

        폴러 재구동·중복 실행이 값을 덮어쓰면 같은 창의 관측이 시점마다 달라져
        시계열이 뒤집힌다. 창은 한 번 찍히면 그 값이 정본이다.
        """
        ...

    def read_observation(
        self, *, publication_id: str, window_index: int
    ) -> tuple[MetricValue, ...]:
        """관측 1건의 지표값 전량. 없으면 빈 튜플."""
        ...

    def save_reward(
        self, *, publication_id: str, reward_value: float | None, formula_version: str
    ) -> None:
        """보상 확정 + `topic_stats` 차분 갱신(원자적).

        `reward_value=None`은 "미확정/표본 부족 → 학습 제외"(FR-L2)다. NULL을 저장하면
        `trials`에서도 빠진다 — 밴딧 집계가 NULL 행을 세지 않는다는 수용기준이 여기서
        성립한다.
        """
        ...

    def read_reward(self, publication_id: str) -> tuple[float | None, str | None] | None:
        """(reward_value, formula_version) 또는 행이 없으면 None.

        **`(None, 'v1')`과 `None`은 다르다** — 앞은 "계산했고 미확정", 뒤는 "아직 안 봤다".
        """
        ...

    def read_topic_stats(self, platform: Platform | None = None) -> tuple[TopicStat, ...]:
        """`ReadStats` 계약 구현 — Topic/Growth 에이전트가 읽는 누적 통계."""
        ...

    def save_playbook(
        self, scope: PlaybookScope, guidance: str, scope_ref: str | None = None
    ) -> PlaybookVersion:
        """`WritePlaybook` 계약 구현 — 같은 scope의 다음 버전으로 append."""
        ...

    def save_analysis_note(
        self, *, cycle_id: str | None, body: str, insufficient_evidence: bool
    ) -> str:
        """LLM 분석글 적재(FR-L5). 검증기 통과는 **호출부 책임** — 여기선 강제하지 않는다.

        검증기(sns.learning.validator)를 이 안에서 부르면 스코어보드 JSON을 store가 알아야
        하고, 그 순간 저장소가 분석 파이프라인을 겸하게 된다.
        """
        ...

    def log_event(
        self,
        *,
        cycle_id: str | None,
        kind: LearningEventKind,
        payload: Mapping[str, object],
    ) -> None:
        """append-only 관측 이벤트. `cycle_id`는 학습 루프에선 대개 None(사이클 밖 실행)."""
        ...


# ── InMemory ────────────────────────────────────────────────────────


@dataclass
class _MemReward:
    value: float | None
    formula_version: str


@dataclass
class _MemStats:
    trials: int = 0
    reward_sum: float = 0.0


@dataclass(frozen=True)
class _MemPlaybook:
    scope: PlaybookScope
    scope_ref: str | None
    version: int
    guidance: str


class InMemoryMetricStore:
    """결정론 테스트·드라이런용. 발행 건은 `add_published_item`으로 시드한다.

    시드 헬퍼가 계약 밖에 있는 것은 의도다 — 운영 경로에서 발행 원장을 만드는 것은
    러너(`CycleStore.create_publication`)와 발행 상태머신이지 이 저장소가 아니다.
    """

    def __init__(self) -> None:
        self.items: dict[str, PublishedItem] = {}
        self.observations: dict[
            tuple[str, int], tuple[datetime | None, tuple[MetricValue, ...]]
        ] = {}
        self.rewards: dict[str, _MemReward] = {}
        self.stats: dict[tuple[str, ContentFormat, Platform], _MemStats] = {}
        self.playbooks: list[_MemPlaybook] = []
        self.notes: list[dict[str, object]] = []
        self.events: list[dict[str, object]] = []
        self._seq = 0

    def _id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq}"

    def add_published_item(self, item: PublishedItem) -> None:
        self.items[item.publication_id] = item

    # ── 계약 ──

    def published_items(
        self, *, since: datetime | None = None, limit: int = 200
    ) -> tuple[PublishedItem, ...]:
        rows = sorted(self.items.values(), key=lambda i: (i.published_at, i.publication_id))
        picked = []
        for item in rows:
            if since is not None and item.published_at < since:
                continue
            windows = tuple(
                sorted(w for (pid, w) in self.observations if pid == item.publication_id)
            )
            picked.append(
                PublishedItem(
                    publication_id=item.publication_id,
                    platform=item.platform,
                    external_post_id=item.external_post_id,
                    published_at=item.published_at,
                    content_format=item.content_format,
                    topic_id=item.topic_id,
                    channel_mode=item.channel_mode,
                    observed_windows=windows,
                )
            )
        return tuple(picked[:limit])

    def save_observation(
        self,
        *,
        publication_id: str,
        window_index: int,
        values: Sequence[MetricValue],
        observed_at: datetime | None = None,
    ) -> str | None:
        key = (publication_id, window_index)
        if key in self.observations:
            return None
        self.observations[key] = (observed_at, tuple(values))
        return self._id("obs")

    def read_observation(
        self, *, publication_id: str, window_index: int
    ) -> tuple[MetricValue, ...]:
        found = self.observations.get((publication_id, window_index))
        return () if found is None else found[1]

    def save_reward(
        self, *, publication_id: str, reward_value: float | None, formula_version: str
    ) -> None:
        item = self.items.get(publication_id)
        prev = self.rewards.get(publication_id)
        self.rewards[publication_id] = _MemReward(reward_value, formula_version)
        if item is None:  # 원장에 없는 건 — Pg에서는 FK가 막는다.
            raise KeyError(f"알 수 없는 publication_id: {publication_id!r}")
        d_trials, d_sum = _stats_delta(
            prev_value=None if prev is None else prev.value, new_value=reward_value
        )
        if d_trials == 0 and d_sum == 0.0:
            return
        cell = self.stats.setdefault(
            (item.topic_id, item.content_format, item.platform), _MemStats()
        )
        cell.trials += d_trials
        cell.reward_sum += d_sum

    def read_reward(self, publication_id: str) -> tuple[float | None, str | None] | None:
        row = self.rewards.get(publication_id)
        return None if row is None else (row.value, row.formula_version)

    def read_topic_stats(self, platform: Platform | None = None) -> tuple[TopicStat, ...]:
        return tuple(
            TopicStat(
                topic_id=topic_id,
                format=fmt,
                platform=plat,
                trials=cell.trials,
                reward_sum=cell.reward_sum,
            )
            for (topic_id, fmt, plat), cell in sorted(self.stats.items())
            if platform is None or plat == platform
        )

    def save_playbook(
        self, scope: PlaybookScope, guidance: str, scope_ref: str | None = None
    ) -> PlaybookVersion:
        version = 1 + max(
            (p.version for p in self.playbooks if p.scope == scope and p.scope_ref == scope_ref),
            default=0,
        )
        self.playbooks.append(
            _MemPlaybook(scope=scope, scope_ref=scope_ref, version=version, guidance=guidance)
        )
        return PlaybookVersion(scope=scope, scope_ref=scope_ref, version=version)

    def save_analysis_note(
        self, *, cycle_id: str | None, body: str, insufficient_evidence: bool
    ) -> str:
        note_id = self._id("note")
        self.notes.append(
            {
                "id": note_id,
                "cycle_id": cycle_id,
                "body": body,
                "insufficient_evidence": insufficient_evidence,
            }
        )
        return note_id

    def log_event(
        self,
        *,
        cycle_id: str | None,
        kind: LearningEventKind,
        payload: Mapping[str, object],
    ) -> None:
        self.events.append({"cycle_id": cycle_id, "kind": kind, "payload": dict(payload)})


def _stats_delta(*, prev_value: float | None, new_value: float | None) -> tuple[int, float]:
    """(trials 증분, reward_sum 증분) — 재계산이 중복 집계되지 않게 차분만 낸다.

    NULL 보상은 표본이 아니다(FR-L2·L3): NULL→값이면 +1, 값→NULL이면 -1.
    """
    d_trials = int(new_value is not None) - int(prev_value is not None)
    d_sum = (new_value or 0.0) - (prev_value or 0.0)
    return d_trials, d_sum


# ── Pg ──────────────────────────────────────────────────────────────

_PUBLISHED_ITEMS_SQL = """
SELECT p.id::text, ch.platform, p.external_post_id, p.published_at,
       ci.format, ci.topic_id::text, ch.mode,
       COALESCE(
           array_agg(mo.window_index ORDER BY mo.window_index)
               FILTER (WHERE mo.window_index IS NOT NULL),
           ARRAY[]::integer[]
       )
  FROM publication p
  JOIN channel ch ON ch.id = p.channel_id
  JOIN content_item ci ON ci.id = p.content_item_id
  LEFT JOIN metric_observation mo ON mo.publication_id = p.id
 WHERE p.status = 'published'
   AND p.external_post_id IS NOT NULL
   AND p.published_at IS NOT NULL
   AND (%(since)s::timestamptz IS NULL OR p.published_at >= %(since)s::timestamptz)
 GROUP BY p.id, ch.platform, p.external_post_id, p.published_at, ci.format, ci.topic_id, ch.mode
 ORDER BY p.published_at, p.id
 LIMIT %(limit)s
"""

# topic_stats 차분 반영 — 대상 (topic, format, platform)은 발행 원장에서 조인해 온다.
# 호출부가 셋을 들고 다니면 러너·폴러·리포트가 각자 조인해 서로 다르게 틀린다.
_BUMP_STATS_SQL = """
INSERT INTO topic_stats (topic_id, format, platform, trials, reward_sum)
SELECT ci.topic_id, ci.format, ch.platform, %(d_trials)s, %(d_sum)s
  FROM publication p
  JOIN content_item ci ON ci.id = p.content_item_id
  JOIN channel ch ON ch.id = p.channel_id
 WHERE p.id = %(pid)s
ON CONFLICT (topic_id, format, platform) DO UPDATE SET
    trials     = topic_stats.trials + EXCLUDED.trials,
    reward_sum = topic_stats.reward_sum + EXCLUDED.reward_sum,
    updated_at = now()
"""


class PgMetricStore:
    """psycopg 백엔드. autocommit 커넥션을 주입받는다(모듈 docstring 참조)."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def published_items(
        self, *, since: datetime | None = None, limit: int = 200
    ) -> tuple[PublishedItem, ...]:
        rows = self._conn.execute(_PUBLISHED_ITEMS_SQL, {"since": since, "limit": limit}).fetchall()
        return tuple(
            PublishedItem(
                publication_id=row[0],
                platform=row[1],
                external_post_id=row[2],
                published_at=row[3],
                content_format=row[4],
                topic_id=row[5],
                channel_mode=row[6],
                observed_windows=tuple(row[7]),
            )
            for row in rows
        )

    def save_observation(
        self,
        *,
        publication_id: str,
        window_index: int,
        values: Sequence[MetricValue],
        observed_at: datetime | None = None,
    ) -> str | None:
        # 관측 헤더와 값을 한 트랜잭션에 — 헤더만 남고 값이 비면 "폴링했는데 전 지표
        # 결측"과 구분되지 않는다. 그 둘은 스코어보드에서 다른 판정을 받는다.
        with self._conn.transaction():
            row = self._conn.execute(
                """
                INSERT INTO metric_observation (publication_id, window_index, observed_at)
                VALUES (%(pid)s, %(w)s, COALESCE(%(at)s::timestamptz, now()))
                ON CONFLICT (publication_id, window_index) DO NOTHING
                RETURNING id::text
                """,
                {"pid": publication_id, "w": window_index, "at": observed_at},
            ).fetchone()
            if row is None:
                return None  # 이미 찍힌 창 — 멱등 흡수(계약 참조).
            observation_id = str(row[0])
            for value in values:
                self._conn.execute(
                    """
                    INSERT INTO metric_value (observation_id, metric_key, value, missing)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (observation_id, metric_key) DO NOTHING
                    """,
                    (observation_id, value.metric_key, value.value, value.missing),
                )
            return observation_id

    def read_observation(
        self, *, publication_id: str, window_index: int
    ) -> tuple[MetricValue, ...]:
        rows = self._conn.execute(
            """
            SELECT mv.metric_key, mv.value, mv.missing
              FROM metric_value mv
              JOIN metric_observation mo ON mo.id = mv.observation_id
             WHERE mo.publication_id = %s AND mo.window_index = %s
             ORDER BY mv.metric_key
            """,
            (publication_id, window_index),
        ).fetchall()
        return tuple(MetricValue(metric_key=row[0], value=row[1], missing=row[2]) for row in rows)

    def save_reward(
        self, *, publication_id: str, reward_value: float | None, formula_version: str
    ) -> None:
        with self._conn.transaction():
            # FOR UPDATE — 같은 건을 두 프로세스가 재계산해도 차분이 한 번만 반영된다.
            prev_row = self._conn.execute(
                "SELECT reward_value FROM reward WHERE publication_id = %s FOR UPDATE",
                (publication_id,),
            ).fetchone()
            self._conn.execute(
                """
                INSERT INTO reward (publication_id, reward_value, formula_version)
                VALUES (%s, %s, %s)
                ON CONFLICT (publication_id) DO UPDATE SET
                    reward_value    = EXCLUDED.reward_value,
                    formula_version = EXCLUDED.formula_version,
                    computed_at     = now()
                """,
                (publication_id, reward_value, formula_version),
            )
            d_trials, d_sum = _stats_delta(
                prev_value=None if prev_row is None else prev_row[0], new_value=reward_value
            )
            if d_trials == 0 and d_sum == 0.0:
                return
            self._conn.execute(
                _BUMP_STATS_SQL,
                {"pid": publication_id, "d_trials": d_trials, "d_sum": d_sum},
            )

    def read_reward(self, publication_id: str) -> tuple[float | None, str | None] | None:
        row = self._conn.execute(
            "SELECT reward_value, formula_version FROM reward WHERE publication_id = %s",
            (publication_id,),
        ).fetchone()
        return None if row is None else (row[0], row[1])

    def read_topic_stats(self, platform: Platform | None = None) -> tuple[TopicStat, ...]:
        rows = self._conn.execute(
            """
            SELECT topic_id::text, format, platform, trials, reward_sum
              FROM topic_stats
             WHERE (%(platform)s::text IS NULL OR platform = %(platform)s)
             ORDER BY topic_id, format, platform
            """,
            {"platform": platform},
        ).fetchall()
        return tuple(
            TopicStat(
                topic_id=row[0], format=row[1], platform=row[2], trials=row[3], reward_sum=row[4]
            )
            for row in rows
        )

    def save_playbook(
        self, scope: PlaybookScope, guidance: str, scope_ref: str | None = None
    ) -> PlaybookVersion:
        # scope_ref가 NULL이면 UNIQUE(scope, scope_ref, version)가 중복을 못 막는다
        # (Postgres에서 NULL은 서로 다르다). 그래서 버전은 여기서 세되, 같은 트랜잭션
        # 안에서 센다 — 학습 루프는 단일 실행자라 이 정도면 충분하다.
        with self._conn.transaction():
            row = self._conn.execute(
                """
                SELECT COALESCE(MAX(version), 0) + 1
                  FROM playbook
                 WHERE scope = %s AND scope_ref IS NOT DISTINCT FROM %s
                """,
                (scope, scope_ref),
            ).fetchone()
            version = int(row[0]) if row is not None else 1
            self._conn.execute(
                """
                INSERT INTO playbook (scope, scope_ref, version, guidance)
                VALUES (%s, %s, %s, %s)
                """,
                (scope, scope_ref, version, guidance),
            )
        return PlaybookVersion(scope=scope, scope_ref=scope_ref, version=version)

    def save_analysis_note(
        self, *, cycle_id: str | None, body: str, insufficient_evidence: bool
    ) -> str:
        row = self._conn.execute(
            """
            INSERT INTO analysis_note (cycle_id, body, insufficient_evidence)
            VALUES (%s, %s, %s)
            RETURNING id::text
            """,
            (cycle_id, body, insufficient_evidence),
        ).fetchone()
        assert row is not None
        return str(row[0])

    def log_event(
        self,
        *,
        cycle_id: str | None,
        kind: LearningEventKind,
        payload: Mapping[str, object],
    ) -> None:
        self._conn.execute(
            "INSERT INTO run_event (cycle_id, kind, payload) VALUES (%s, %s, %s)",
            (cycle_id, kind, Json(dict(payload))),
        )


# mypy(sns): 두 구현이 계약을 구조적으로 만족함을 강제 — 시그니처가 갈리면 CI에서 잡힌다.
_check_inmemory: MetricStore = InMemoryMetricStore()
# 동결된 툴 계약 2종(FR-C2)을 이 저장소가 그대로 만족한다 — 에이전트는 store를 모른 채
# read_stats/write_playbook만 받는다.
_check_read_stats: ReadStats = InMemoryMetricStore().read_topic_stats
_check_write_playbook: WritePlaybook = InMemoryMetricStore().save_playbook


def _check_pg(store: PgMetricStore) -> MetricStore:
    return store
