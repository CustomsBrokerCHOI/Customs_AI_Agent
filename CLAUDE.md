# Customs AI Agent

HS CODE 자동 품목분류 SaaS. 관세사의 분류 의사결정을 보조하는 AI 도구. 관세청 UNIPASS API·CLIP 포털 데이터를 RAG 기반으로 검색하고 LLM 으로 후보 HS 부호를 검증하여 분류의견서 초안을 생성한다.

> **중요**: 본 시스템의 모든 분류 결과는 **초안(Draft)** 상태로 제공되며, 관세사의 최종 확인·서명 없이 세관 신고에 사용할 수 없다. UI·PDF·API 응답에서 이 면책을 항상 명시한다.

## 제품 범위

- **사용자**: 관세사 (단독 사용자 혹은 B2B 팀)
- **입력**: 품명 + 상세 설명 + 사진(선택, Claude Vision)
- **출력**: HS CODE 후보 + 근거 인용(주·해설서 원문) + 분류의견서 초안(Draft)
- **MVP 범위**: 단일 품목 분류 초안 생성. 일괄 처리·관세사 서명 워크플로우는 후속 스프린트.
- **데이터 소스**: 관세청 UNIPASS API (HS 부호·관세율·통계부호·환율·화물통관) + CLIP 포털 (해설서·관세율표·품목분류 사례). **WCO 영문 해설서는 수집 범위 외** (CLIP 해설서의 영문 필드로 대체).

## 기술 스택

- **Backend**: Python 3.10+, FastAPI
- **Frontend**: Next.js (관세사 분류 워크플로우 UI)
- **DB**: PostgreSQL + pgvector (HS 부호·해설서 임베딩)
- **Scraping**: Playwright (CLIP 포털)
- **External API**: UNIPASS (관세청 전자통관시스템, 서비스별 키)
- **Test**: pytest
- **Deploy**: Railway

## Repository Layout

- `data/` — UNIPASS 캐시(`data/cache/`), 호출 사용량 로그(`data/usage/`), 마스터 CSV. 대용량·민감 데이터 gitignore.
- `prompts/` — LLM 프롬프트 템플릿 (5단계 분류 알고리즘의 단계별 프롬프트).
- `scripts/` — Python 모듈 (API 클라이언트, ETL, 데이터 적재, 분류 엔진).
- `tests/` — pytest 단위 테스트.
- `api/` — (계획) FastAPI 서버 (분류 엔드포인트).
- `web/` — (계획) Next.js 프런트엔드.
- `reports/` — 로컬 분석 산출물 (개발용).

## UNIPASS API

- 엔드포인트: `https://unipass.customs.go.kr:38010/ext/rest/<serviceName>/<operation>`
- 인증키(crkyCn)는 환경변수 `UNIPASS_API_KEY` 로만 주입한다. 코드/로그에 평문으로 남기지 않는다.
- 기본 클라이언트: `scripts/unipass_client.py` 의 `UnipassClient` 를 재사용한다.
- 응답 캐싱: `UnipassClient(cache_dir="data/cache")` 로 opt-in. 파일 기반, SHA256 키, 기본 TTL 7일. `call(..., force_refresh=True)` 로 우회 가능.
- 확인된 주요 서비스명:
  - HS 부호 조회: `hsSgnQry/searchHsSgn` (실측 확정 2026-04-21)
    - 필수 파라미터: `hsSgn`(10자리), `koenTp`(`1`=한글, `2`=영문)
    - 응답: `<hsSgnSrchRtnVo>` → `<hsSgnSrchRsltVo>` 다건(FTA 별). 행 태그: `hsSgn`, `korePrnm`, `englPrnm`, `txrt`, `txtpSgn`, `qtyUt`, `wghtUt`
    - 에러 시: `<ntceInfo>` 에 메시지, `<tCnt>-1</tCnt>`
  - 관세율 조회: `trrtQry/retrieveTrrt` (API030, 실측 확정 2026-04-21). 필수 `hsSgn`, 옵션 `trrtTpcd`. 루트 `<trrtQryRtnVo>` → 행 `<trrtQryRsltVo>` (소문자 시작, 가이드 PDF 와 다름). 필드: `hsSgn, trrtTpcd, trrtTpNm, trrt, prutXamt, basePrc, aplyStrtDt, aplyEndDt`. 기본세율은 `trrtTpcd=A`.
  - 관세환율 정보: `trifFxrtInfoQry/retrieveTrifFxrtInfo` (API012, 실측 확정 2026-04-21). 필수 `qryYymmDd`+`imexTp`(1=수출,2=수입). 루트 `<trifFxrtInfoQryRtnVo>` → 행 `<trifFxrtInfoQryRsltVo>`. 필드: `cntySgn, mtryUtNm`(가이드 docs의 `mtryUtlNm`은 오류)`, fxrt, currSgn, aplyBgnDt, imexTp`.
  - 통계부호 내역 조회: `statsSgnQry/retrieveStatsSgnBrkd` (API019, 실측 확정 2026-04-21). 필수 `statsSgnTp` (A01~A13). **중요**: 타입마다 row 태그와 필드가 다름(A01=`<othStatsSgnQryVo>`, A06=`<statsSgnQryVo2>`). 범용 파서가 태그명→값 dict 반환.
  - 화물통관 진행: `cargCsclPrgsInfoQry/retrieveCargCsclPrgsInfo` (API001, 엔드포인트만 확정). 필수 `cargMtNo` 또는 `mblNo/hblNo+blYy`. 응답: `items` (단건 상세 / 다건 목록) + `events` (단건일 때 처리이력). 데이터는 최근 3년 이내.
- **품목분류 사례는 UNIPASS API 미제공** → CLIP 스크래퍼(`openULS0203042S.do`)로 수집한다 (Phase 1-B 진행 예정).
- **관세율표**: UNIPASS `retrieve_trrt` 를 주 소스로 사용. CLIP `fetch_tariff_schedule` (`openULS0201002Q.do`) 는 표준화된 품명·탄력세율 구분을 위한 보조 소스.
- 응답 XML 태그명은 로그인 후 연계가이드 PDF 에만 공개되므로, 신규 서비스 연동 시
  실 호출로 구조를 확인한 뒤 파서를 확정한다.

## Conventions

- Python 3.10+, 외부 호출은 `requests`, XML 응답은 `xml.etree.ElementTree` 로 파싱.
- 비밀값(.env, API 키)은 저장소에 커밋 금지. 서비스별 UNIPASS 키는 `UNIPASS_API_KEY_<SERVICE>` 규칙.
- 신규 Python 모듈은 `python -m scripts.<name>` 형태로 실행 가능하도록 작성.
- **5단계 분류 알고리즘 철칙**: LLM 에 주·해설서·호 용어 원문을 반드시 DB 에서 직접 주입. LLM 자유 생성 금지(할루시네이션 방지). 프롬프트 인젝션 방어를 기본으로 포함한다.
- 분류 결과는 UI·API·PDF 어디에서든 **"초안(Draft)" 상태** 를 명시한다.

## Branching

작업 브랜치는 `claude/<topic>-<slug>` 규칙을 사용하고, 기본 개발 브랜치는 별도 지정 시 해당 브랜치로 푸시한다.
