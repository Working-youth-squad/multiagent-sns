"""토큰 앱레벨 암호화 (NFR-7, T0-5).

플랫폼 액세스 토큰은 평문 저장 금지. `channel.token_encrypted`(bytea)에는
암호문을, `channel.token_key_version`(integer)에는 이 암호에 쓰인 키 버전을
함께 저장해 향후 키 회전 시 어떤 키로 복호할지 식별한다.

대칭 암호는 cryptography의 Fernet(AES-128-CBC + HMAC) — 인증 암호이므로
변조 시 복호가 실패한다. 키는 `Config.encryption_key`(부팅 시 필수).
"""

from cryptography.fernet import Fernet, InvalidToken

from sns.config import Config


class DecryptionError(RuntimeError):
    """복호 실패 — 키 불일치 또는 암호문 변조."""


def generate_key() -> str:
    """운영용 새 Fernet 키 발급(APP_ENCRYPTION_KEY에 넣을 값)."""
    return Fernet.generate_key().decode()


class TokenCipher:
    """단일 키 버전 암복호. 키 회전은 버전별 인스턴스로 확장(향후)."""

    def __init__(self, key: str, key_version: int) -> None:
        self._fernet = Fernet(key.encode())
        self.key_version = key_version

    @classmethod
    def from_config(cls, config: Config) -> "TokenCipher":
        return cls(config.encryption_key, config.encryption_key_version)

    def encrypt(self, plaintext: str) -> tuple[bytes, int]:
        """평문 → (암호문 bytes, 키 버전). 반환 그대로 두 컬럼에 저장."""
        token = self._fernet.encrypt(plaintext.encode())
        return token, self.key_version

    def decrypt(self, token: bytes) -> str:
        try:
            return self._fernet.decrypt(token).decode()
        except InvalidToken as exc:
            raise DecryptionError("토큰 복호 실패 — 키 불일치 또는 변조") from exc
