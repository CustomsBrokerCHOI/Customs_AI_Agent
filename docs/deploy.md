# 배포 가이드 (Railway)

관세사용 Customs AI Agent 를 Railway 에 배포하는 절차.
현재 MVP 단계이므로 **단일 리전 · 단일 인스턴스** 기준.

---

## 1. 사전 준비

| 항목 | 설명 |
|---|---|
| Railway 계정 | https://railway.app 가입 후 결제 수단 연결 |
| GitHub 저장소 | 본 레포를 Railway 와 연동 |
| API 키 3종 | Anthropic / OpenAI / UNIPASS (서비스별) |
| 도메인 (선택) | Railway 기본 도메인 또는 커스텀 CNAME |

---

## 2. 서비스 구조

```
┌─────────────────────────────┐
│ Railway Project             │
│ ┌─────────┐   ┌───────────┐ │
│ │ api     │──▶│ Postgres  │ │
│ │ (this)  │   │ + pgvector│ │
│ └─────────┘   └───────────┘ │
│      ▲                      │
│      │ BackgroundTasks      │
│      │ (없음 — API 프로세스 내부) │
└──────┼──────────────────────┘
       │
   GitHub Actions (CI)
```

---

## 3. 1회차 셋업

### 3.1. Postgres 플러그인 프로비저닝

1. Railway Dashboard → **New → Database → PostgreSQL**.
2. 생성 후 **Variables** 탭에서 `DATABASE_URL` 확인.
3. **pgvector 확장 활성**:
   ```sql
   -- Railway → Postgres → Data → Query
   CREATE EXTENSION IF NOT EXISTS vector;
   ```
   (Alembic 마이그레이션도 `CREATE EXTENSION IF NOT EXISTS vector` 를 포함하나,
   Railway 신규 인스턴스에서 superuser 권한이 필요할 수 있어 수동 실행 권장.)

### 3.2. API 서비스 생성

1. **New → GitHub Repo** → 본 저장소 선택.
2. Root directory: `/` (저장소 루트 — `railway.toml` 이 빌드를 주관).
3. 첫 배포 대기.

### 3.3. 환경변수 주입

**Project Variables** 에 다음을 등록:

| 키 | 예시 / 주의 |
|---|---|
| `ENV` | `production` |
| `DATABASE_URL` | **Postgres 플러그인이 주입한 값에서 `postgresql://` → `postgresql+asyncpg://` 로 수정.** 예: `postgresql+asyncpg://postgres:<pw>@<host>:<port>/railway` |
| `JWT_SECRET` | `openssl rand -hex 32` 결과값 |
| `ANTHROPIC_API_KEY` | `sk-ant-...` |
| `OPENAI_API_KEY` | `sk-...` |
| `UNIPASS_API_KEY` | 기본 |
| `UNIPASS_API_KEY_HSSGNQRY` | 서비스별 |
| `UNIPASS_API_KEY_TRRTQRY` | 서비스별 |
| `RATE_LIMIT_PER_DAY` | `100` (기본) |
| `LOG_LEVEL` | `INFO` |

> **주의**: `.env.example` 을 참고하되 **값은 Railway Variables 에만** 저장. 코드/로그에 평문 금지.

### 3.4. 첫 마이그레이션

`railway.toml` 의 `startCommand` 가 배포마다 `alembic upgrade head` 를 자동 수행.
로그에서 `INFO [alembic.runtime.migration] Running upgrade 0001 -> 0002` 등을 확인.

---

## 4. 이어지는 배포

- GitHub 의 기본 브랜치(`main` 또는 지정 브랜치) push → Railway 가 자동 빌드·배포.
- 수동 배포: Railway Dashboard → Service → **Deploy**.
- Rollback: Railway Deployments 목록에서 이전 배포 **"Redeploy"**.

---

## 5. PDF 렌더 (weasyprint) 시스템 의존

`railway.toml` 의 `nixpacksPlan.phases.setup.aptPkgs` 에 다음이 포함되어야 한다:

```
libpango-1.0-0
libpangoft2-1.0-0
libcairo2
```

빌드 로그에서 `Installing apt packages: libpango ...` 가 보이면 정상.
누락 시 `/classify/{id}/report.pdf` 가 501 로 응답 — `docs/scraping-policy.md` 의 PDF fallback 안내 참조.

---

## 6. 프런트엔드 (Next.js)

현재 `web/` 은 별도 서비스로 배포하거나 로컬 개발 용도.
Railway 에 올릴 경우:

1. 두 번째 서비스(`web-next`) 생성.
2. Root directory: `/web`.
3. 환경변수:
   - `NEXT_PUBLIC_API_BASE_URL` = `https://<api-service>.up.railway.app`
4. Build: `npm install && npm run build`, Start: `npm start`.

---

## 7. 첫 관세사 계정 생성

배포 직후 DB 에 user 가 없다. 방법 2가지:

- **A (권장)**: `POST /auth/register` 를 `curl` 로 호출.
  ```bash
  curl -X POST https://<api>.up.railway.app/auth/register \
    -H 'content-type: application/json' \
    -d '{"email":"broker@firm.kr","password":"StrongPass1!","name":"홍길동"}'
  ```
- **B**: Railway Postgres Query 탭에서 직접 INSERT (bcrypt 해시 필요, 비권장).

---

## 8. 관찰·운영 체크리스트

- `/health` 엔드포인트가 200 이면 DB 연결 정상.
- `data/usage/YYYYMMDD.jsonl` 은 **컨테이너 파일시스템에만** 저장되어 재배포 시 소실.
  운영 전환 시 Railway Volume 연결 또는 외부 저장소(S3 등) 로 승격 필요.
- LLM 호출 비용은 Anthropic/OpenAI 대시보드 + `meta.usage` 로 교차 확인.

---

## 9. 트러블슈팅

| 증상 | 원인 | 대응 |
|---|---|---|
| 빌드 중 `weasyprint`/`cffi` 실패 | apt 패키지 누락 | `railway.toml` `aptPkgs` 재확인 |
| Alembic "Can't locate revision" | 마이그레이션 파일 누락 | `git log api/alembic/versions/` 비교 + 재배포 |
| 500 on `/classify` | `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` 미주입 → 실엔진 실패 | 키 확인 (미주입 시 mock fallback 되도록 라우터에 방어 있음) |
| 423 Locked 지속 | 계정 잠금 (5회 실패 → 15분) | Retry-After 대기 또는 DB `locked_until=NULL` 수동 해제 |
| pgvector 쿼리 에러 | 확장 미활성 | `CREATE EXTENSION vector` 수동 실행 |

---

## 10. 운영 단계별 승격 체크리스트

- [ ] `JWT_SECRET` 32바이트 랜덤
- [ ] Postgres 백업 스케줄 활성 (`docs/backup-recovery.md`)
- [ ] Railway 프로덕션 환경 분리 (`ENV=production`)
- [ ] 비관리 트래픽 rate limit (reverse proxy / Cloudflare)
- [ ] `/docs` Swagger 비공개 토글 (FastAPI `docs_url=None` when `ENV=production`)
- [ ] 사용량 로그 외부 저장소 이관
- [ ] 관세청 CLIP 스크래핑 robots.txt 재확인 (`python -m scripts.dev_probe_robots`)
