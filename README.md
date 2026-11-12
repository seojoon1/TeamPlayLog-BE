# TeamPlayLog-BE

팀플로그 백엔드 (FastAPI). 역할 약속과 동료 확인 기록으로 팀플 평가의 근거를 남기는 앱.

## 개발 실행

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # macOS/Linux: .venv/bin/python
cp .env.example .env
.venv/Scripts/python -m uvicorn app.main:app --reload
```

- API 문서: http://localhost:8000/docs
- 개발 DB는 SQLite(`teamplaylog.db`)이며 첫 실행 때 테이블이 만들어진다.
- 시연 데이터: `.venv/Scripts/python scripts/seed_demo.py` (4인 팀, 비밀번호는 파일 안 `DEMO_PASSWORD`)

### 휴대폰(Expo Go)에서 접속할 때

휴대폰은 PC의 `localhost`에 접속할 수 없으므로, 모든 네트워크에서 받도록 띄운다.

```bash
.venv/Scripts/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- PC와 휴대폰이 같은 와이파이에 있어야 한다.
- Windows 방화벽이 8000번 포트를 막으면, 처음 실행할 때 뜨는 허용 창에서 "개인 네트워크"를 허용한다.

## 테스트

```bash
.venv/Scripts/python -m pytest
```

## DB 마이그레이션 (Alembic)

모델(`app/models.py`)을 바꾸면 마이그레이션을 만든다. SQLite 개발 DB는 서버가 테이블을 자동으로 만들지만, 배포(PostgreSQL)는 마이그레이션으로만 바뀐다.

```bash
.venv/Scripts/python -m alembic revision --autogenerate -m "무엇을 바꿨는지"
.venv/Scripts/python -m alembic upgrade head
```

## 배포 (Render + Neon 예시)

1. Neon에서 PostgreSQL DB를 만들고 연결 주소(`postgresql://...?sslmode=require`)를 복사한다.
2. Render › New › Blueprint 에서 이 레포를 고른다. `render.yaml`대로 Docker 서버가 만들어진다.
3. Render 대시보드의 Environment에 `DATABASE_URL`을 붙여 넣는다. `JWT_SECRET`은 Render가 임의로 만든다.
4. 배포되면 시작할 때 `alembic upgrade head`가 자동으로 돌고 서버가 뜬다. `https://<서비스 이름>.onrender.com/health`가 `{"ok": true}`면 성공.

알아 둘 점

- 결과물 파일과 리포트 PDF는 서버 디스크에 저장하지 않는다(파일은 DB에, PDF는 요청 때마다 생성). 무료 서버가 재시작해도 사라지지 않는다.
- Render 무료 서버는 15분 동안 요청이 없으면 잠든다. 깨어날 때 첫 요청이 30초쯤 걸리고, 잠든 동안에는 마감 알림 같은 주기 작업도 돌지 않는다. 시연 직전에 한 번 열어 두면 된다.
- `DATABASE_URL`이 PostgreSQL인데 `JWT_SECRET`이 개발용 기본값이면 서버가 시작을 거부한다.
