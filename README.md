# TeamPlayLog-BE

팀플로그 백엔드 (FastAPI). 역할 약속과 동료 확인 기록으로 팀플 평가의 근거를 남기는 앱.

## 실행

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # macOS/Linux: .venv/bin/python
cp .env.example .env
.venv/Scripts/python -m uvicorn app.main:app --reload
```

- API 문서: http://localhost:8000/docs
- 개발 DB는 SQLite(`teamplaylog.db`)이며 첫 실행 때 테이블이 만들어진다.
- 배포 시 `DATABASE_URL`을 PostgreSQL로 바꾼다.

## 테스트

```bash
.venv/Scripts/python -m pytest
```
