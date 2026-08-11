import pytest

from sns.config import (
    ENV_DATABASE_URL,
    ENV_ENCRYPTION_KEY,
    ENV_ENCRYPTION_KEY_VERSION,
    Config,
)
from sns.crypto import DecryptionError, TokenCipher, generate_key


def _cipher(version: int = 1) -> TokenCipher:
    return TokenCipher(generate_key(), version)


def test_encrypt_decrypt_round_trip() -> None:
    cipher = _cipher()
    token, version = cipher.encrypt("IGQVJ-secret-access-token")
    assert version == 1
    assert isinstance(token, bytes)
    assert cipher.decrypt(token) == "IGQVJ-secret-access-token"


def test_ciphertext_is_not_plaintext() -> None:
    # 평문이 암호문에 그대로 노출되지 않는다 (NFR-7)
    token, _ = _cipher().encrypt("super-secret")
    assert b"super-secret" not in token


def test_encrypt_returns_configured_key_version() -> None:
    token, version = _cipher(version=7).encrypt("t")
    assert version == 7


def test_decrypt_with_wrong_key_fails() -> None:
    token, _ = _cipher().encrypt("t")
    with pytest.raises(DecryptionError):
        _cipher().decrypt(token)  # 다른 키


def test_decrypt_rejects_tampered_ciphertext() -> None:
    token, _ = (cipher := _cipher()).encrypt("t")
    with pytest.raises(DecryptionError):
        cipher.decrypt(token[:-1] + bytes([token[-1] ^ 0x01]))


def test_from_config() -> None:
    cfg = Config.from_env(
        {
            ENV_DATABASE_URL: "postgresql://sns:sns@localhost:5432/sns",
            ENV_ENCRYPTION_KEY: generate_key(),
            ENV_ENCRYPTION_KEY_VERSION: "2",
        }
    )
    cipher = TokenCipher.from_config(cfg)
    token, version = cipher.encrypt("t")
    assert version == 2
    assert cipher.decrypt(token) == "t"
