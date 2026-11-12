from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import DEV_JWT_SECRET, get_settings
from app.db import Base, engine
from app.errors import AppError, app_error_handler, http_error_handler, validation_error_handler
from app.jobs import start_scheduler
from app.routers import auth, notifications, records, reports, tasks, teams


@asynccontextmanager
async def lifespan(_: FastAPI):
    s = get_settings()
    if s.is_sqlite:
        # 개발용(SQLite): 테이블이 없으면 만든다. 배포(PostgreSQL)는 시작 전에 `alembic upgrade head`를 돌린다.
        Base.metadata.create_all(engine)
    elif s.jwt_secret == DEV_JWT_SECRET or len(s.jwt_secret) < 32:
        raise RuntimeError("배포 환경에서는 JWT_SECRET을 32자 이상의 임의 문자열로 설정해야 합니다.")
    sched = start_scheduler() if s.scheduler_enabled else None
    yield
    if sched:
        sched.shutdown(wait=False)


app = FastAPI(
    title="팀플로그 API",
    version="0.1.0",
    description="역할 약속과 동료 확인 기록으로 팀플 평가의 근거를 남기는 앱의 백엔드",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(StarletteHTTPException, http_error_handler)

for r in (auth, teams, tasks, records, notifications, reports):
    app.include_router(r.router, prefix="/api/v1")


@app.get("/health", tags=["운영"])
def health():
    return {"ok": True}
