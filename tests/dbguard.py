"""테스트 DB 판별·안전장치 (테스트 전용 — `sns/` 런타임은 이걸 쓰지 않는다).

conftest가 스키마를 DROP하고 테이블을 TRUNCATE하므로, DSN이 개발/운영 DB를 가리키면
작업 중이던 원장이 통째로 사라진다. 그래서 파괴적 작업 직전에 `require_test_dsn`으로
**dbname이 `_test`로 끝나는지** 확인하고, `derive_test_dsn`로 개발 DSN에서 테스트 DSN을
기계적으로 파생시킨다(팀원이 따로 환경변수를 외우지 않아도 되게).
"""

from urllib.parse import urlsplit, urlunsplit

TEST_DB_SUFFIX = "_test"


class UnsafeTestDsnError(RuntimeError):
    """테스트가 파괴적 작업을 하기엔 위험한 DSN — 개발/운영 DB로 보인다."""


def database_name(dsn: str) -> str:
    """DSN에서 dbname만. 쿼리 파라미터는 제외."""
    return urlsplit(dsn).path.lstrip("/")


def admin_dsn_for(dsn: str) -> str:
    """같은 서버의 유지보수 DB(`postgres`) DSN — 테스트 DB를 CREATE할 때 붙는 곳.

    CREATE DATABASE는 만들려는 DB 안에서는 실행할 수 없어 다른 DB에 붙어야 한다.
    """
    return urlunsplit(urlsplit(dsn)._replace(path="/postgres"))


def derive_test_dsn(dsn: str) -> str:
    """개발 DSN → 같은 서버의 테스트 DB DSN. 이미 테스트 DB면 그대로(멱등)."""
    parts = urlsplit(dsn)
    name = parts.path.lstrip("/")
    if name.endswith(TEST_DB_SUFFIX):
        return dsn
    return urlunsplit(parts._replace(path=f"/{name}{TEST_DB_SUFFIX}"))


def require_test_dsn(dsn: str) -> None:
    """dbname이 `_test`로 끝나지 않으면 거부. 파괴적 작업 직전에 호출한다."""
    name = database_name(dsn)
    if not name.endswith(TEST_DB_SUFFIX):
        raise UnsafeTestDsnError(
            f"테스트가 '{name}' DB를 지우려 합니다 — 이름이 '{TEST_DB_SUFFIX}'로 끝나야 합니다. "
            f"개발 DB를 날리는 사고를 막기 위한 방어입니다. "
            f"DATABASE_URL은 그대로 두세요 — conftest가 '{name}{TEST_DB_SUFFIX}'를 자동으로 씁니다."
        )
