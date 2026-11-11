from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./teamplaylog.db"
    # 개발 전용 기본값. 배포 시 .env의 JWT_SECRET으로 반드시 바꾼다.
    jwt_secret: str = "dev-only-secret-change-me-before-deploying-0000"
    # 개발용 웹 미리보기(Expo web) 주소. 앱(Android)은 CORS와 상관없다.
    cors_origins: list[str] = ["http://localhost:8081", "http://127.0.0.1:8081"]
    upload_dir: str = "./uploads"
    report_dir: str = "./reports"
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
