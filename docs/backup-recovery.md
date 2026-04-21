# PostgreSQL 백업·복구 절차

관세사 분류 데이터(`classify_jobs`·`audit_logs`·`note_chunks` 등)와 사용자
계정(`users`) 의 **무손실 복원** 을 보장하기 위한 운영 절차.

---

## 1. 목표 (RPO / RTO)

| 지표 | 값 | 근거 |
|---|---|---|
| **RPO** (복구 지점 목표) | **≤ 24시간** | 일 단위 스냅샷 + 분류 이력은 재실행 가능한 성격 |
| **RTO** (복구 시간 목표) | **≤ 2시간** | Railway 플러그인 기준 DB 복원 소요 + Alembic 재적용 |
| **보존 기간** | **30일** 일일 스냅샷, **12개월** 월말 스냅샷 (콜드 아카이브) |

유상 SaaS 단계로 승격하면 RPO 1시간 (WAL archive) + 다중 리전으로 재정의.

---

## 2. Railway 자동 백업

Railway Postgres 플러그인은 **일일 스냅샷** 을 기본 제공 (유료 플랜).

1. Railway Dashboard → Postgres 서비스 → **Backups** 탭.
2. **Enable Scheduled Backups** 활성, 보존 기간 30일 선택.
3. 복원은 동 탭에서 **Restore** 버튼 → 새 Postgres 인스턴스로 전개.

### 한계

- 무료 플랜은 자동 백업 없음 → **유료 승격 필수** 또는 pg_dump 수동.
- 스냅샷은 Railway 인프라에 국한. 재해 대비 원격지 사본은 별도 절차(§3).

---

## 3. 외부 백업 (pg_dump → S3/Google Drive)

운영자 로컬/CI 에서 **주 1회 이상** 실행.

### 3.1. dump 명령

```bash
# 환경변수 DATABASE_URL 은 Railway Postgres 의 "External" URL
# (Railway → Postgres → Connect → "Public Networking" 에서 확인)
pg_dump \
  --no-owner --no-privileges \
  --format=custom \
  --file="customs-$(date -u +%Y%m%dT%H%M%SZ).dump" \
  "$DATABASE_URL"
```

파일 크기는 `note_chunks.embedding` Vector(1536) 비중이 커서
해설서 N건당 대략 **6 KB/청크 × 청크 수** 로 가늠.

### 3.2. 원격 업로드 (예: AWS S3)

```bash
aws s3 cp customs-*.dump s3://<bucket>/db/ --sse AES256
aws s3api put-object-lock-configuration ...  # 30일 retention (권장)
```

### 3.3. 무결성 검증

```bash
pg_restore --list customs-*.dump | head -20
```

"PostgreSQL database dump" 헤더 + 예상 테이블 11개 (users/audit_logs/
hs_codes/tariff_rates/explanatory_notes/note_chunks/classification_cases/
classify_jobs/embedding_versions + alembic_version) 이 보이면 정상.

---

## 4. 복구 절차

### 시나리오 A: 단일 레코드 실수 삭제

1. **신규 Postgres 인스턴스** 에 최신 스냅샷 복원 (Railway Backups 또는 `pg_restore`).
2. 영향받은 테이블만 SELECT 후 원본 인스턴스에 INSERT.
3. 프로덕션 교체 없음 — 수 분 내 완료.

### 시나리오 B: 전체 DB 손상

1. **배포 즉시 중단** — API 서비스 Stop.
2. Railway Backups 에서 가장 근접 스냅샷 **Restore** → 새 인스턴스 주소 획득.
3. API 서비스 Variables 에서 `DATABASE_URL` 을 새 인스턴스 주소로 교체
   (`postgresql+asyncpg://` 프로토콜 유지).
4. `alembic -c api/alembic.ini upgrade head` 로 스키마 최신화 확인.
5. API 재배포.
6. 관세사 공지 + audit 로그로 누락 요청 재집행.

### 시나리오 C: Railway 자체 장애

1. 외부 S3 사본에서 pg_dump 다운로드.
2. 대체 Postgres (Supabase/Neon/AWS RDS 등) 에 `pg_restore`:
   ```bash
   createdb customs_restored
   psql -d customs_restored -c 'CREATE EXTENSION vector'
   pg_restore --no-owner --no-privileges -d customs_restored customs-*.dump
   ```
3. API 서비스를 새 클라우드에 재배포 (docker image + 환경변수).
4. DNS 전환 (CNAME TTL 을 평소 300 으로 유지).

---

## 5. 정기 DR 훈련 체크리스트

분기(3개월) 마다 실행:

- [ ] pg_dump 1회 성공 (시간·용량 기록)
- [ ] S3 업로드 + 무결성 (`pg_restore --list`) 통과
- [ ] **복구 훈련 (readonly)**: 샘플 스냅샷을 신규 인스턴스에 복원 → 핵심 쿼리 5종 실행
  - `SELECT COUNT(*) FROM classify_jobs WHERE created_at > now() - interval '7 days'`
  - `SELECT COUNT(*) FROM note_chunks WHERE embedding IS NOT NULL`
  - `SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT 5`
  - pgvector 검색 샘플 (HNSW 인덱스 동작 확인)
  - Alembic `current` 버전 일치
- [ ] RTO 실측 (시작 → 쿼리 성공까지) — 2시간 초과 시 원인 분석 + 재적재
- [ ] 결과 문서 `docs/dr-YYYY-Q<N>.md`

---

## 6. 백업 제외 / 별도 관리 데이터

| 항목 | 이유 | 대안 |
|---|---|---|
| `data/raw/clip/` | 대용량 원본 HTML, 재스크래핑 가능 | `.gitignore`. 필요 시 S3 cold storage |
| `data/cache/` | UNIPASS 응답 캐시 — TTL 후 재호출 | 재호출 비용 감수 |
| `data/usage/*.jsonl` | 컨테이너 파일시스템 — 재배포 시 소실 | Railway Volume 또는 외부 로그 수집(예: Logtail) 승격 |
| `.env` | 시크릿 — 백업 금지 | `SECURITY.md` 로테이션 섹션 참조 |

---

## 7. 법적 보존 요건 (관세사 서비스 특성)

- 관세 관련 서류는 **관세법 제12조** 상 5년 보존 의무 (수입신고서 기준).
- 본 시스템의 분류의견서(Draft)는 관세사가 수입신고에 인용하면 **증빙 원본** 이 됨.
- `accepted_hs_code` 가 채택된 `classify_jobs` 는 **최소 5년 보존** — 삭제 금지.
- 일반 `audit_logs` 는 운영 목적 2년 보존 권장.

향후 자동 purge job 도입 시 위 기준을 파티셔닝·보존 정책으로 반영.
