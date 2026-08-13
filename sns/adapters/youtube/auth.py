"""유튜브 OAuth — 데스크톱 앱 플로우로 refresh token 확보·재사용 (YT-1·YT-2).

오늘은 token.json 로컬 파일(gitignored)에 저장한다. DB `channel.token_encrypted`
(`sns.crypto.TokenCipher`) 영속은 channel repo와 함께 후속 — 이 함수의 시그니처는
그때도 유지된다(저장 위치만 바뀜).
"""

from pathlib import Path
from typing import Any

SCOPES = ("https://www.googleapis.com/auth/youtube.upload",)


def load_credentials(client_secret: Path, token: Path) -> Any:
    """저장된 토큰이 있으면 refresh, 없으면 브라우저 동의 플로우 후 저장.

    반환값은 `google.oauth2.credentials.Credentials` — googleapiclient의
    `build(..., credentials=...)`에 그대로 넘긴다.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if token.exists():
        creds = Credentials.from_authorized_user_file(  # type: ignore[no-untyped-call]
            str(token), list(SCOPES)
        )
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), list(SCOPES))
        creds = flow.run_local_server(port=0)
    token.write_text(creds.to_json(), encoding="utf-8")
    return creds


def build_youtube(credentials: Any) -> Any:
    """YouTube Data API v3 클라이언트."""
    from googleapiclient.discovery import build

    return build("youtube", "v3", credentials=credentials)
