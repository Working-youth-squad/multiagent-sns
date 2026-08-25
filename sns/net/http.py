"""공용 HTTP 보일러플레이트 — opener 주입점 + 응답 크기 상한.

`google_trends`가 처음 세운 규율(순수 파서 + 얇은 fetch + 주입 opener + 소켓 타임아웃)
을 인증/POST 소스들이 공유한다. 테스트는 `opener`에 가짜를 주입해 네트워크 없이 돈다.

원래 `sns.research.sources._http`였는데, 이미지 트랙(`sns.render.images`)도 같은 규율이
필요해지면서 올렸다 — 렌더가 리서치의 사설 모듈을 들여다보게 두는 것보다 낫다.
"""

import urllib.request
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Protocol


class _Response(Protocol):
    def read(self, amt: int = ..., /) -> bytes: ...


# urllib 계열 opener 주입점. target은 URL 문자열 또는 urllib.request.Request.
Opener = Callable[..., AbstractContextManager[_Response]]

DEFAULT_OPENER: Opener = urllib.request.urlopen

# 외부 응답 크기 상한 — 악의/오작동 소스의 메모리·파싱 DoS 방어(google_trends와 동일).
MAX_RESPONSE_BYTES = 5_000_000


def fetch_bytes(
    target: object, *, timeout_s: float, opener: Opener, max_bytes: int = MAX_RESPONSE_BYTES
) -> bytes:
    """opener로 target을 열어 상한까지 읽는다. 소켓 타임아웃이 소스별 상한을 강제.

    `max_bytes`는 JSON 응답(기본 5MB)과 이미지 바이트가 같은 상한을 쓰지 않게 하는
    조절점이다 — 사진은 JSON보다 훨씬 크고, 그렇다고 기본값을 올리면 파싱 DoS 방어가 헐거워진다.
    """
    with opener(target, timeout=timeout_s) as resp:
        return resp.read(max_bytes)
