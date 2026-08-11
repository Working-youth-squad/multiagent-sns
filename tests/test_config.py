import pytest

from sns.config import (
    ENV_DATABASE_URL,
    ENV_ENCRYPTION_KEY,
    ENV_ENCRYPTION_KEY_VERSION,
    Config,
    ConfigError,
)
from sns.crypto import generate_key

_VALID = {
    ENV_DATABASE_URL: "postgresql://sns:sns@localhost:5432/sns",
    ENV_ENCRYPTION_KEY: generate_key(),  # 유효한 Fernet 키 (형식 검증 통과)
}


def test_from_env_loads_all_fields() -> None:
    cfg = Config.from_env({**_VALID, ENV_ENCRYPTION_KEY_VERSION: "3"})
    assert cfg.database_url == _VALID[ENV_DATABASE_URL]
    assert cfg.encryption_key == _VALID[ENV_ENCRYPTION_KEY]
    assert cfg.encryption_key_version == 3


def test_key_version_defaults_to_one() -> None:
    assert Config.from_env(_VALID).encryption_key_version == 1


def test_missing_encryption_key_fails_startup() -> None:
    # NFR-7: 키 부재 시 기동 실패 — 평문 저장 경로 차단
    with pytest.raises(ConfigError, match=ENV_ENCRYPTION_KEY):
        Config.from_env({ENV_DATABASE_URL: _VALID[ENV_DATABASE_URL]})


def test_empty_value_is_rejected() -> None:
    with pytest.raises(ConfigError):
        Config.from_env({**_VALID, ENV_ENCRYPTION_KEY: ""})


def test_malformed_encryption_key_fails_startup() -> None:
    # NFR-7: 존재하지만 형식 무효인 키도 기동에서 차단 (첫 발행 시점 raw ValueError 방지)
    with pytest.raises(ConfigError, match=ENV_ENCRYPTION_KEY):
        Config.from_env({**_VALID, ENV_ENCRYPTION_KEY: "not-a-valid-fernet-key"})


def test_missing_database_url_fails() -> None:
    with pytest.raises(ConfigError, match=ENV_DATABASE_URL):
        Config.from_env({ENV_ENCRYPTION_KEY: _VALID[ENV_ENCRYPTION_KEY]})


@pytest.mark.parametrize("bad", ["0", "-1", "abc"])
def test_invalid_key_version_rejected(bad: str) -> None:
    with pytest.raises(ConfigError):
        Config.from_env({**_VALID, ENV_ENCRYPTION_KEY_VERSION: bad})
