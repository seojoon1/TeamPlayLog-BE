from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_JWT_SECRET = "dev-only-secret-change-me-before-deploying-0000"


class Settings(BaseSettings):
    # .env (직접 쓴 값) 다음에 .env.local (neon init / neon env pull 이 쓰는 값)을 읽는다. 뒤의 파일이 이긴다.
    model_config = SettingsConfigDict(env_file=(".env", ".env.local"), extra="ignore")

    database_url: str = "sqlite:///./teamplaylog.db"
    # 풀러를 거치지 않는 직접 연결 주소 (Neon). 있으면 마이그레이션은 이 주소로 돈다.
    database_url_unpooled: str | None = None

    @field_validator("database_url", "database_url_unpooled")
    @classmethod
    def _psycopg(cls, v: str | None) -> str | None:
        # Neon·Render가 주는 postgres:// 주소를 SQLAlchemy psycopg 드라이버 주소로 바꾼다
        if v is None:
            return v
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix) :]
        return v

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    # 개발 전용 기본값. 배포 시 .env의 JWT_SECRET으로 반드시 바꾼다.
    jwt_secret: str = DEV_JWT_SECRET
    # 개발용 웹 미리보기(Expo web) 주소. 앱(Android)은 CORS와 상관없다.
    cors_origins: list[str] = ["http://localhost:8081", "http://127.0.0.1:8081"]
    scheduler_enabled: bool = True
    access_token_minutes: int = 60
    refresh_token_days: int = 14

    # 기능 규칙 기본값 (기능명세서 4장). 운영 중 바꿀 수 있게 설정으로 둔다.
    invite_code_days: int = 7
    remind_before_hours: int = 24
    confirm_wait_hours: int = 72
    report_view_wait_hours: int = 48
    quiet_start_hour: int = 23  # 이 시각부터
    quiet_end_hour: int = 8  # 이 시각까지 알림을 모아 둔다 (KST)
    login_fail_limit: int = 5
    login_lock_minutes: int = 10


@lru_cache
def get_settings() -> Settings:
    return Settings()
