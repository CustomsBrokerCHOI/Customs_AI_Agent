# Customs AI Agent — 진행 계획서

체크리스트 형식 로드맵. 완료 항목은 `[x]`, 진행 중은 `[~]`, 대기는 `[ ]`, 범위 제외는 `[-]`.

## 제품 정의 (PRD v1.0 · 2026-04-21 채택)

- **제품**: HS CODE 자동 품목분류 SaaS (관세사 최종 확인 보조 도구)
- **출력**: 5단계 알고리즘 기반 HS CODE **초안(Draft)** + 근거 조항 인용 + 분류의견서
- **스택**: Python 3.10+ FastAPI (backend) · Next.js (frontend) · PostgreSQL + pgvector (DB) · Playwright (scraping) · Railway (deploy)
- **5단계 분류 알고리즘**:
  1. 물품 식별 (Input Gate) — 품명·사진·설명 → 재질·용도·기능 구조화
  2. 부(Section) 결정 + pgvector 검색 — 후보 HS Priority Queue
  3. 부-류 일치 검증 (Verification Gate) — 불일치 시 Queue 다음 후보
  4. 호 용어·주·해설서 RAG 검증 (Deep Verify) — DB 원문 직접 주입
  5. 불일치 → 재시도 Loop — 소진 시 "분류 불확실" 플래그 + 관세사 검토 요청
- **범위 제외**: WCO 영문 해설서 별도 수집 (CLIP 해설서 영문 필드로 대체), 외국 분류 사례(미국/EU/일본 등)

---

## Phase 0 — 프로젝트 셋업

- [x] 디렉토리 구조 (`data/`, `prompts/`, `scripts/`, `reports/`)
- [x] `CLAUDE.md` 작성 (UNIPASS 컨벤션, 브랜치 규칙)
- [x] `requirements.txt` (requests, pandas, playwright)
- [x] `scripts/__init__.py` 패키지화
- [x] `.env.example` 작성 (`UNIPASS_API_KEY`, `LOG_LEVEL` 등)
- [x] `.gitignore` 보강 (`.env`, `__pycache__/`, `data/raw/`, `reports/*.html`)
- [ ] `pyproject.toml` 또는 `setup.cfg` 로 lint/format 도구 통합 (ruff, black)

## Phase 1 — 데이터 수집부

### 1-A. UNIPASS API 클라이언트
- [x] 저수준 `UnipassClient.call()` (세션, 타임아웃, 업무 오류 처리)
- [x] `search_hs_sgn()` — 엔드포인트 `hsSgnQry/searchHsSgn`
- [x] 공식 엔드포인트 검증 (관세청 레퍼런스 코드)
- [x] **실제 API 키로 `search_hs_sgn()` 1회 호출 → XML 응답 태그명 확정** (2026-04-21, `scripts/dev_probe_hs.py`)
- [x] `search_hs_sgn()` 다건 반환 지원 (FTA 별 `<hsSgnSrchRsltVo>` 전체 수집, `txrt`/`txtpSgn` 포함) (2026-04-21, `list[dict]` 반환으로 breaking change)
- [x] `retrieve_trrt()` (관세율 조회, `trrtQry/retrieveTrrt`) 메서드 추가 (2026-04-21). 가이드 PDF 의 Pascal `<Trrt...>` 는 실응답에서 camel `<trrt...>`. 서비스별 별도 인증키(`UNIPASS_API_KEY_TRRTQRY`) 필요.
- [x] `retrieve_trif_fxrt_info()` (관세환율) 메서드 추가 (2026-04-21). 실측 58통화 확인 (USD/EUR/JPY/CNY 등).
- [x] `retrieve_stats_sgn_brkd()` (통계부호 내역) 메서드 추가 (2026-04-21). 타입별 스키마 상이로 범용 dict 파서 사용.
- [x] `retrieve_carg_cscl_prgs_info()` (화물통관 진행) 메서드 추가 (2026-04-21). `items`+`events`+`notice` 반환. 실호출 검증 완료 (HBL `HKG1257231`/blYy=2026, items 1건 + events 12건).
- [x] 응답 캐싱 레이어 (`data/cache/<service>_<operation>_<hash>.xml`, 파일 기반, SHA256 키, TTL 7일) (2026-04-21)
- [x] 호출 한도 모니터링 (`data/usage/<YYYYMMDD>.jsonl`, `daily_limits` dict, 80% warning, 초과 시 UnipassError) (2026-04-21)

### 1-B. CLIP 스크래퍼
- [x] Playwright 기반 골격
- [x] HS해설서(`openULS0202001Q.do`) 셀렉터 실측 확정
- [x] `fetch_explanatory_note()` — 통칙/부/류/호 × 국문/영문 수집
- [x] 실제 호 1건 수집 → DataFrame 저장까지 end-to-end 통합 테스트 (2026-04-21, heading 8471 year 2022, 4탭×2언어 수집, 9 청크). `scripts/dev_probe_clip.py`.
- [x] **관세율표** 스크래퍼 추가 (`openULS0201002Q.do`) — `ClipScraper.fetch_tariff_schedule()` + `TariffLine` dataclass (2026-04-21). HS 8471 기준 34개 세번 추출 검증. UNIPASS `retrieve_trrt` 와 데이터 일부 중복이나 표준화된 품명·탄력세율 구분 제공.
- [ ] **품목분류 사례** 스크래퍼 추가 (`openULS0203042S.do`) — PRD 핵심 데이터. 분류 알고리즘 2단계(검색) 입력.
- [-] ~~외국 분류 사례 (미국/EU/일본 등) 수집~~ — PRD 범위 제외
- [-] ~~WCO 영문 해설서 별도 수집~~ — CLIP 해설서의 영문 필드로 대체
- [ ] FAQ (`openULS0206017Q.do`) 수집 (소스 다양화용, 후순위)
- [ ] robots.txt 및 이용약관 검토 결과 문서화
- [ ] 수집 성공/실패 로깅 + 재시도 정책

### 1-C. 데이터 저장
- [x] `DataManager.upsert_items()` — `data/item_master.csv` dedupe
- [x] `DataManager.chunk_explanatory_note()` — 토큰 단위 슬라이딩 윈도우
- [x] `scripts/build_item_master.py` — UNIPASS `search_hs_sgn` → `item_master.csv` E2E 파이프라인 (2026-04-21). HS 한·영 이중 호출, FTA 'A' 기본세율 추출, dedupe 업서트.
- [x] `tiktoken` 기반 정확한 토큰 카운팅으로 교체 (2026-04-21, `cl100k_base` 기본, 미설치/실패 시 공백 fallback). 실측: 한국어 해설서에서 공백 방식 9 청크 → tiktoken 40 청크 (평균 901자).
- [ ] 해설서 raw HTML 보관 (`data/raw/clip/<heading>.html`) — 재처리용
- [ ] 수집 메타 로그 (`data/manifest.jsonl`) — 호별 수집 일시, 버전, 청크 수

## Phase 2 — RAG 전처리 (pgvector 기반)

- [ ] PostgreSQL + pgvector 로컬/도커 환경 구축 (문서 기재, 실행은 사용자)
- [x] 스키마 설계 — 9 테이블 `api/db/models.py` + Alembic 초기 마이그레이션 `api/alembic/versions/20260421_0001_*.py` (users/audit_logs/hs_codes/tariff_rates/explanatory_notes/note_chunks/classification_cases/classify_jobs/embedding_versions, HNSW cosine 인덱스 2개) (2026-04-21)
- [ ] 청크 메타 스키마 확정 (heading, kind, hsk_version, lang, chunk_index, source)
- [ ] 임베딩 모델 선정 (`bge-m3-ko` 또는 `text-embedding-3-large`). 한국어 품명·해설서 성능 기준.
- [ ] 임베딩 파이프라인 (`scripts/build_index.py`) — CLIP 청크 + 품목분류 사례 + HS 품명 적재
- [ ] pgvector 인덱스 구축 (HNSW 또는 IVFFlat, cosine distance)
- [ ] 검색 평가셋 구축 (질문-정답 호 매핑 50건)
- [ ] Recall@5 / MRR 측정 스크립트

## Phase 3 — 분류 엔진 (5단계 알고리즘)

### 3-A. 물품 식별 (Input Gate)
- [ ] 입력 스키마 정의 (품명·상세설명 필수, 사진 URL 선택)
- [ ] Claude Vision + LLM 으로 재질·용도·제조방법·주요 기능 구조화 추출
- [ ] 물품 특성 JSON 출력 스키마
- [ ] 정보 부족 시 추가 질문 생성 로직

### 3-B. 부(Section) 결정 + pgvector 검색 (Search)
- [ ] 21개 부(Section) 메타데이터 로드 및 부 결정 LLM 호출
- [ ] pgvector 키워드 1~3개 동시 검색 (품명·설명·특성)
- [ ] 후보 HS CODE Priority Queue 자료구조
- [ ] 관세청 품목분류 DB + 해설서 DB 병렬 조회

### 3-C. 부-류 일치 검증 (Verification Gate)
- [ ] 후보 앞 2자리(류) → 결정된 부 소속 여부 확인
- [ ] 불일치 시 Queue 다음 후보로 진행
- [ ] Queue 소진 시 부 재결정 또는 사용자 추가 정보 요청
- [ ] 무한루프 방어 (최대 후보 수 · 재시도 회수 제한) — `/review` 필수

### 3-D. 호 용어·주·해설서 RAG 검증 (Deep Verify)
- [ ] DB 에서 호 용어 원문 + 부/류 주 조항 + 해설서 단락 조회
- [ ] LLM 프롬프트: 원문만 인용, 자유 생성 금지 (할루시네이션 방지)
- [ ] 일치·불일치 항목 + 근거 조항 구조화 출력
- [ ] 환각 방지 가드 (인용 호 번호가 검색 결과에 없으면 reject)
- [ ] 프롬프트 인젝션 방어 (사용자 입력 샌드박스)

### 3-E. Retry Loop + 최종 처리
- [ ] 불일치 → Queue 현재 후보 제거 후 3-B 재진입
- [ ] 모든 후보 소진 → "분류 불확실" 플래그 + 불확실 이유 기록 → 관세사 검토 요청
- [ ] 결과는 항상 **Draft** 상태로 표시
- [ ] 토큰·비용 모니터링 (`data/usage/` 확장 또는 별도)

### 3-F. LLM 클라이언트
- [ ] Anthropic Claude API 통합 (Vision + 텍스트)
- [ ] 필요 시 OpenAI 토글
- [ ] 재시도·타임아웃·rate-limit 처리

## Phase 4 — API + UI

### 4-A. FastAPI 백엔드
- [x] 프로젝트 구조 `api/` (routers, services, schemas, db, core, alembic) + FastAPI 앱 + `/health` + Pydantic Settings (2026-04-21)
- [ ] `POST /classify` — 단건 분류 요청 (비동기 잡 or 동기 응답)
- [ ] `GET /classify/{id}` — 상태·결과 조회
- [ ] `GET /hs/{hs_code}` — HS 부호 마스터 + 관세율 + 해설서 링크
- [ ] 인증 (JWT 세션, 관세사 계정)
- [ ] Rate limit · 감사 로그
- [ ] OpenAPI 스키마 자동 생성

### 4-B. Next.js 프런트엔드
- [ ] 관세사 분류 워크플로우 화면 (입력 → 단계별 시각화 → 최종 결과)
- [ ] **분류 과정 시각화**: 부→류→호 단계 표시 (PRD `/plan-design-review` 핵심 포인트)
- [ ] Draft 상태 + 면책 문구 UI 노출 (분류 결과 어디서든)
- [ ] 근거 조항 인라인 인용 (해설서·주 원문 팝오버)
- [ ] 분류의견서 PDF 다운로드

### 4-C. 분류의견서 템플릿
- [ ] 구조 정의 (품명, HS CODE, 적용 법령, 근거 조항, 관세율, Draft 표시)
- [ ] Markdown → PDF 변환
- [ ] (후속) 관세사 서명란·서명 워크플로우

## Phase 5 — 품질·보안·운영

### 5-A. 테스트
- [~] 단위 테스트 (`tests/`) — pytest 기반 (2026-04-21 기본 인프라 완성, 20/20 통과)
  - [x] `DataManager.upsert_items` dedupe 검증
  - [x] `chunk_explanatory_note` 경계 케이스 (tiktoken + whitespace fallback)
  - [x] `UnipassClient` 키 조회/캐시 경로/한도 검사 (네트워크 없음)
  - [ ] `UnipassClient.call()` 모킹 테스트 (HTTP 응답 주입)
- [ ] 분류 엔진 5단계 단위 테스트 (LLM 응답 모킹)
- [ ] FastAPI 엔드포인트 통합 테스트
- [ ] CLIP 스크래퍼 통합 테스트 (실 사이트 호출, 별도 마커)
- [ ] E2E Playwright 테스트 (분류 워크플로우)

### 5-B. 보안 (PRD `/cso` 감사 항목)
- [ ] 프롬프트 인젝션 방어 (3-D 항목과 연계)
- [ ] 관세사 인증·권한 관리 (JWT 세션, 계정 잠금)
- [ ] 법적 데이터(주·해설서 원문) RAG 프롬프트 감사
- [ ] OWASP Top 10 점검
- [ ] 시크릿 관리 (.env, Railway env vars, GitHub Secrets)

### 5-C. 운영
- [ ] GitHub Actions CI (lint + 단위 테스트 + 보안 스캔)
- [ ] Railway 배포 파이프라인
- [ ] PostgreSQL 백업·복구 절차
- [ ] HS Code 개정(5년 주기) 대응 자동 감지·알림
- [ ] 모니터링 (분류 정확도, 처리 시간, LLM 토큰 비용)

## 결정 보류 항목 (TBD)

- [x] 관세율표 데이터 출처 → **UNIPASS `retrieve_trrt` 주, CLIP `fetch_tariff_schedule` 보조** (둘 다 확보)
- [x] 벡터 DB → **pgvector 확정** (PRD)
- [x] 사용자 인증·접근 제어 → **필요 (관세사 B2B)**. Phase 4-A 에서 JWT
- [ ] HSK 버전 관리 정책 (2022 / 2017 등 멀티 버전 동시 보유 여부)
- [ ] 다국어 응답 지원 범위 (국문 우선, 영문 MVP 외)
- [ ] 분류의견서 관세사 서명 워크플로우 MVP 포함 여부 (현재 MVP 외로 잠정)
- [ ] 임베딩 모델 최종 선정 (bge-m3-ko vs text-embedding-3-large, Phase 2 에서 벤치마크 후 결정)
