"""GitHub 트렌딩 — 개발 니치 리포지토리 (§2 소스 #5, 무인증 HTML).

공식 API가 없어 트렌딩 페이지 HTML에서 `owner/repo`를 뽑는다(관용적 스크레이프).
파싱 실패는 예외로 전파되고 서비스가 해당 소스만 격리한다 — 마크업이 바뀌어도
리서치 전체는 죽지 않는다. 순서 보존 중복 제거.
"""

import re

from sns.net.http import DEFAULT_OPENER, MAX_RESPONSE_BYTES, Opener

GITHUB_TRENDING_URL = "https://github.com/trending?since=daily"

# 트렌딩 리포 제목: <h2 ...><a href="/owner/repo" ...>. owner/repo만 캡처.
_REPO_RE = re.compile(r'<h2\b[^>]*>\s*<a\b[^>]*href="/([^"/]+/[^"]+?)"', re.DOTALL)


def parse_github_trending(data: bytes) -> tuple[str, ...]:
    html_text = data.decode("utf-8", errors="replace")
    seen: set[str] = set()
    out: list[str] = []
    for repo in _REPO_RE.findall(html_text):
        name = repo.strip().rstrip("/")
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return tuple(out)


def fetch_github_trending(
    limit: int,
    *,
    url: str = GITHUB_TRENDING_URL,
    timeout_s: float = 10.0,
    opener: Opener = DEFAULT_OPENER,
) -> tuple[str, ...]:
    with opener(url, timeout=timeout_s) as resp:
        data = resp.read(MAX_RESPONSE_BYTES)
    return parse_github_trending(data)[:limit]
