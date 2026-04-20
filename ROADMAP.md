# Customs AI Agent — 진행 계획서

체크리스트 형식 로드맵. 완료 항목은 `[x]`, 진행 중은 `[~]`, 대기는 `[ ]`.

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
- [ ] **실제 API 키로 `search_hs_sgn()` 1회 호출 → XML 응답 태그명 확정**
- [ ] `retrieve_trrf_info()` (관세율기본조회) 메서드 추가 — API 신청 후 응답 태그 검증 필요
- [ ] `retrieve_trif_fxrt_info()` (관세환율) 메서드 추가
- [ ] `retrieve_stats_sgn_brkd()` (통계부호) 메서드 추가
- [ ] `retrieve_carg_cscl_prgs_info()` (화물통관 진행) 메서드 추가
- [ ] 응답 캐싱 레이어 (예: SQLite 또는 `data/cache/<service>_<hash>.xml`)
- [ ] 호출 한도 모니터링 (UNIPASS 일일 호출 수 제한 대응)

### 1-B. CLIP 스크래퍼
- [x] Playwright 기반 골격
- [x] HS해설서(`openULS0202001Q.do`) 셀렉터 실측 확정
- [x] `fetch_explanatory_note()` — 통칙/부/류/호 × 국문/영문 수집
- [ ] 실제 호 1건 수집 → DataFrame 저장까지 end-to-end 통합 테스트
- [ ] **관세율표** 스크래퍼 추가 (`openULS0201002Q.do`) — UNIPASS 미제공
- [ ] **품목분류 사례** 스크래퍼 추가 (`openULS0203042S.do`)
- [ ] 외국 분류 사례 (미국/EU/일본 등) 선택적 수집 모듈
- [ ] FAQ (`openULS0206017Q.do`) 수집 (소스 다양화용)
- [ ] robots.txt 및 이용약관 검토 결과 문서화
- [ ] 수집 성공/실패 로깅 + 재시도 정책

### 1-C. 데이터 저장
- [x] `DataManager.upsert_items()` — `data/item_master.csv` dedupe
- [x] `DataManager.chunk_explanatory_note()` — 토큰 단위 슬라이딩 윈도우
- [ ] `tiktoken` 기반 정확한 토큰 카운팅으로 교체
- [ ] 해설서 raw HTML 보관 (`data/raw/clip/<heading>.html`) — 재처리용
- [ ] 수집 메타 로그 (`data/manifest.jsonl`) — 호별 수집 일시, 버전, 청크 수

## Phase 2 — RAG 전처리

- [ ] 청크 메타 스키마 확정 (heading, kind, hsk_version, lang, chunk_index)
- [ ] 임베딩 모델 선정 (예: `bge-m3-ko`, `text-embedding-3-large`)
- [ ] 벡터 DB 선정 (Qdrant / Chroma / pgvector)
- [ ] 임베딩 파이프라인 (`scripts/build_index.py`)
- [ ] 검색 평가셋 구축 (질문-정답 호 매핑 50건)
- [ ] Recall@5 / MRR 측정 스크립트

## Phase 3 — LLM 추론 엔진

- [ ] LLM 클라이언트 추상화 (Anthropic / OpenAI 토글)
- [ ] 프롬프트 템플릿 작성 (`prompts/`)
  - [ ] `classification.md` — HS 분류 추론
  - [ ] `risk_assessment.md` — 리스크 판정
  - [ ] `summarization.md` — 해설서 요약
- [ ] 분류 추론 파이프라인: 품목명 입력 → 후보 호 검색 → LLM 판정 → 근거 인용
- [ ] 환각 방지 가드 (인용 호 번호가 검색 결과에 없으면 reject)
- [ ] 토큰/비용 모니터링 로거

## Phase 4 — 리포팅

- [ ] 단일 품목 분류 리포트 템플릿 (`reports/<date>/classification_<id>.md`)
- [ ] 일괄 분류 결과 CSV 출력
- [ ] HTML 리포트 (관세율 표, 근거 해설 인용 포함)
- [ ] 운영 대시보드 (정확도, 처리량, 비용 추이)

## Phase 5 — 품질·운영

- [ ] 단위 테스트 (`tests/`) — pytest 기반
  - [ ] `UnipassClient.call()` 모킹 테스트
  - [ ] `DataManager.upsert_items` dedupe 검증
  - [ ] `chunk_explanatory_note` 경계 케이스
- [ ] CLIP 스크래퍼 통합 테스트 (실 사이트 호출, 별도 마커)
- [ ] GitHub Actions CI (lint + 단위 테스트)
- [ ] 시크릿 관리 정책 (.env, GitHub Secrets)
- [ ] 운영 환경 배포 방안 (서버리스 vs 컨테이너)
- [ ] 데이터 백업·복구 절차

## 결정 보류 항목 (TBD)

- [ ] 관세율표 데이터 출처: UNIPASS 관세율기본조회 API vs CLIP 스크래퍼(`openULS0201002Q.do`) vs 공공데이터포털
- [ ] HSK 버전 관리 정책 (2022 / 2017 등 멀티 버전 동시 보유 여부)
- [ ] 사용자 인증·접근 제어 필요 여부
- [ ] 다국어 응답 지원 범위 (국문 only vs 국문+영문)
