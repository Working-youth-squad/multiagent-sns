"""앱 설정 — 환경변수에서 로드. 필수 시크릿 부재/무효 시 기동 실패 (NFR-7, T0-5).

`Config.from_env()`은 앱 부팅 시 1회 호출한다. 암호화 키가 없거나 형식이 무효면
여기서 `ConfigError`로 즉시 실패시켜, 평문 토큰이 DB에 들어가는 경로 자체를 막는다.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass

# 환경변수 이름 (단일 출처)
ENV_DATABASE_URL = "DATABASE_URL"
ENV_ENCRYPTION_KEY = "APP_ENCRYPTION_KEY"
ENV_ENCRYPTION_KEY_VERSION = "APP_ENCRYPTION_KEY_VERSION"


class ConfigError(RuntimeError):
    """필수 설정 부재/무효 — 기동 실패 신호."""


@dataclass(frozen=True)
class Config:
    database_url: str
    # 앱레벨 토큰 암호화 키 (Fernet, base64 32바이트) + 버전 (NFR-7, 회전 대비)
    encryption_key: str
    encryption_key_version: int

    @staticmethod
    def from_env(env: Mapping[str, str] | None = None) -> "Config":
        source: Mapping[str, str] = os.environ if env is None else env
        return Config(
            database_url=_require(source, ENV_DATABASE_URL),
            encryption_key=_encryption_key(source),
            encryption_key_version=_key_version(source),
        )


def _require(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if not value:  # None 또는 빈 문자열 모두 거부
        raise ConfigError(f"필수 환경변수 없음: {name}")
    return value


def _encryption_key(env: Mapping[str, str]) -> str:
    """존재만이 아니라 Fernet 키로 유효한지까지 검증한다.

    부재/빈값뿐 아니라 형식 무효(길이·base64 오류)도 여기서 `ConfigError`로
    막아, 무효 키가 부팅을 통과한 뒤 첫 발행 시점에 raw `ValueError`로 터지는
    경로를 차단한다 (NFR-7의 '기동 실패' 보증을 무효 키까지 확장).
    """
    value = _require(env, ENV_ENCRYPTION_KEY)
    from cryptography.fernet import Fernet

    try:
        Fernet(value.encode())
    except (ValueError, TypeError) as exc:
        raise ConfigError(
            f"{ENV_ENCRYPTION_KEY}가 유효한 Fernet 키가 아님 (base64 32바이트 필요)"
        ) from exc
    return value


def _key_version(env: Mapping[str, str]) -> int:
    raw = env.get(ENV_ENCRYPTION_KEY_VERSION, "1")
    try:
        version = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{ENV_ENCRYPTION_KEY_VERSION}는 정수여야 함: {raw!r}") from exc
    if version < 1:
        raise ConfigError(f"{ENV_ENCRYPTION_KEY_VERSION}는 1 이상이어야 함: {version}")
    return version
