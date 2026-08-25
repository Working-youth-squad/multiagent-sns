"""온보딩 위저드 영속화 계약 — 채널 목록/생성 + 프로필 append-only 저장.

[sns.web.approve.store] 규율과 동형: Protocol + InMemory(결정론 테스트) +
Pg(autocommit 커넥션 주입). 프로필 개정은 UPDATE가 아니라 새 행(FR-W2 이력 보존),
현재 프로필 = 채널별 최신 행.

온보딩이 만드는 채널의 mode 기본값은 'hybrid' — 사람 검수 관문을 기본으로 두고,
auto 전환은 운영 판단으로만 한다(FR-E1).
"""

from dataclasses import dataclass
from typing import Protocol

import psycopg
from psycopg.types.json import Json

from sns.onboarding.profile import ChannelProfile, parse_profile, profile_to_json


@dataclass(frozen=True)
class ChannelRow:
    channel_id: str
    platform: str
    handle: str
    mode: str


class OnboardingStore(Protocol):
    """온보딩 웹 앱이 의존하는 유일한 영속화 계약."""

    def list_channels(self) -> tuple[ChannelRow, ...]: ...
    def create_channel(self, *, platform: str, handle: str) -> str: ...
    def save_profile(self, channel_id: str, profile: ChannelProfile) -> None: ...
    def latest_profile(self, channel_id: str) -> ChannelProfile | None: ...


class InMemoryOnboardingStore:
    """결정론 테스트용. revision 이력을 그대로 노출해 앱 테스트가 검증."""

    def __init__(self, channels: tuple[ChannelRow, ...] = ()) -> None:
        self._channels: list[ChannelRow] = list(channels)
        self.profiles: dict[str, list[ChannelProfile]] = {}  # channel_id -> revision 순서

    def list_channels(self) -> tuple[ChannelRow, ...]:
        return tuple(self._channels)

    def create_channel(self, *, platform: str, handle: str) -> str:
        channel_id = f"ch-{len(self._channels) + 1}"
        self._channels.append(
            ChannelRow(channel_id=channel_id, platform=platform, handle=handle, mode="hybrid")
        )
        return channel_id

    def save_profile(self, channel_id: str, profile: ChannelProfile) -> None:
        self.profiles.setdefault(channel_id, []).append(profile)

    def latest_profile(self, channel_id: str) -> ChannelProfile | None:
        revisions = self.profiles.get(channel_id)
        return revisions[-1] if revisions else None


class PgOnboardingStore:
    """psycopg 백엔드. autocommit 커넥션을 주입받는다([sns.publish.stores] 규율)."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def list_channels(self) -> tuple[ChannelRow, ...]:
        rows = self._conn.execute(
            "SELECT id, platform, handle, mode FROM channel ORDER BY created_at, handle"
        ).fetchall()
        return tuple(
            ChannelRow(channel_id=str(r[0]), platform=str(r[1]), handle=str(r[2]), mode=str(r[3]))
            for r in rows
        )

    def create_channel(self, *, platform: str, handle: str) -> str:
        row = self._conn.execute(
            "INSERT INTO channel (platform, handle, mode) VALUES (%s, %s, 'hybrid') RETURNING id",
            (platform, handle),
        ).fetchone()
        assert row is not None
        return str(row[0])

    def save_profile(self, channel_id: str, profile: ChannelProfile) -> None:
        self._conn.execute(
            "INSERT INTO channel_profile (channel_id, profile) VALUES (%s, %s)",
            (channel_id, Json(profile_to_json(profile))),
        )

    def latest_profile(self, channel_id: str) -> ChannelProfile | None:
        row = self._conn.execute(
            "SELECT profile FROM channel_profile WHERE channel_id = %s "
            "ORDER BY created_at DESC LIMIT 1",
            (channel_id,),
        ).fetchone()
        return None if row is None else parse_profile(row[0])


# mypy(sns): 두 구현이 계약 OnboardingStore를 구조적으로 만족함을 강제.
_check_inmemory: OnboardingStore = InMemoryOnboardingStore()


def _check_pg(store: PgOnboardingStore) -> OnboardingStore:
    return store
