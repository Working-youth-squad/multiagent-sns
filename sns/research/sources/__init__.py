"""트렌드 소스 fetcher (04-트렌드조사 §2). 각 fetcher는 `SourceFetcher` 시그니처
(`limit -> tuple[str, ...]`)를 만족하고, 실패는 예외로 던진다 — 격리는 서비스 몫."""
