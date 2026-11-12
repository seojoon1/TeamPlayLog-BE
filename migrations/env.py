"""Alembic 실행 환경. DB 주소와 테이블 정의는 앱 코드에서 가져온다."""

from logging.config import fileConfig

from alembic import context

from app import models  # noqa: F401  (모든 테이블을 Base.metadata에 등록)
from app.config import get_settings
from app.db import Base, make_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _migration_url() -> str:
    # Neon처럼 풀러 주소와 직접 주소가 따로 있으면, 스키마 변경은 직접 주소로 한다
    s = get_settings()
    return s.database_url_unpooled or s.database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_migration_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = make_engine(_migration_url())
    with engine.connect() as connection:
        # SQLite에서도 열 변경이 되도록 batch 모드를 켠다
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
