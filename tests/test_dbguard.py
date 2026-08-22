"""테스트 DB 안전장치 — 개발/운영 DB를 테스트가 파괴하는 사고를 코드가 막는다.

conftest는 스키마를 DROP하고 테이블을 TRUNCATE한다. DSN이 개발 DB를 가리킨 채로
pytest를 돌리면 작업 중이던 사이클·콘텐츠 원장이 통째로 사라진다(실제로 겪은 사고).
"""

import pytest

from tests.dbguard import (
    UnsafeTestDsnError,
    admin_dsn_for,
    database_name,
    derive_test_dsn,
    require_test_dsn,
)


def test_derives_test_dsn_by_suffixing_dbname() -> None:
    assert (
        derive_test_dsn("postgresql://sns:sns@localhost:5432/sns")
        == "postgresql://sns:sns@localhost:5432/sns_test"
    )


def test_derivation_is_idempotent() -> None:
    already = "postgresql://sns:sns@localhost:5432/sns_test"
    assert derive_test_dsn(already) == already


def test_derivation_preserves_query_params() -> None:
    assert (
        derive_test_dsn("postgresql://u:p@host:5432/app?sslmode=require")
        == "postgresql://u:p@host:5432/app_test?sslmode=require"
    )


def test_guard_rejects_non_test_dsn() -> None:
    with pytest.raises(UnsafeTestDsnError, match="sns"):
        require_test_dsn("postgresql://sns:sns@localhost:5432/sns")


def test_guard_accepts_test_dsn() -> None:
    require_test_dsn("postgresql://sns:sns@localhost:5432/sns_test")


def test_guard_rejects_dbname_merely_containing_test() -> None:
    """'testbed' 같은 이름은 테스트 DB가 아니다 — 접미사로만 판정한다."""
    with pytest.raises(UnsafeTestDsnError):
        require_test_dsn("postgresql://u:p@host:5432/testbed")


def test_admin_dsn_points_at_maintenance_database() -> None:
    """테스트 DB를 CREATE하려면 그 DB가 아닌 곳에 붙어야 한다."""
    assert (
        admin_dsn_for("postgresql://sns:sns@localhost:5432/sns_test")
        == "postgresql://sns:sns@localhost:5432/postgres"
    )


def test_admin_dsn_preserves_query_params() -> None:
    assert (
        admin_dsn_for("postgresql://u:p@host:5432/app_test?sslmode=require")
        == "postgresql://u:p@host:5432/postgres?sslmode=require"
    )


def test_database_name_extracted_without_query() -> None:
    assert database_name("postgresql://u:p@host:5432/app_test?sslmode=require") == "app_test"
