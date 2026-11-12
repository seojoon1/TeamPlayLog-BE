import pytest

from app.config import normalize_db_url

PG = "postgresql+psycopg://u:p@ep-x-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"


@pytest.mark.parametrize(
    "raw",
    [
        "postgresql://u:p@ep-x-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require",
        "postgres://u:p@ep-x-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require",
        "  postgresql://u:p@ep-x-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require\n",
        "'postgresql://u:p@ep-x-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require'",
        "psql 'postgresql://u:p@ep-x-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require'",
        '"postgresql://u:p@ep-x-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"',
    ],
)
def test_pasted_neon_urls_are_cleaned(raw):
    assert normalize_db_url(raw) == PG


def test_sqlite_untouched():
    assert normalize_db_url("sqlite:///./teamplaylog.db") == "sqlite:///./teamplaylog.db"
    assert normalize_db_url(None) is None


def test_garbage_gives_clear_error_without_leaking_value():
    with pytest.raises(ValueError) as e:
        normalize_db_url("ep-x.neon.tech/neondb secretpassword")
    assert "secretpassword" not in str(e.value)
