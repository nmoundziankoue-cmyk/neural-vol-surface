import pytest

from app.config import _normalize_database_url


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("postgres://u:p@host:5432/db", "postgresql+psycopg2://u:p@host:5432/db"),
        ("postgresql://u:p@host:5432/db", "postgresql+psycopg2://u:p@host:5432/db"),
        ("postgresql+psycopg2://u:p@host:5432/db", "postgresql+psycopg2://u:p@host:5432/db"),
    ],
)
def test_normalize_database_url(raw, expected):
    assert _normalize_database_url(raw) == expected
