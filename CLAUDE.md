# Customs AI Agent

관세 업무 자동화를 위한 AI 에이전트 프로젝트. UNIPASS(관세청 전자통관시스템) API 를 연동하여 통관 데이터를 수집, 분석, 리포팅한다.

## Repository Layout

- `data/` — UNIPASS API 응답 캐시, 원본 수집 데이터(HS 코드, 관세율, 수출입 실적 등). 대용량 원본은 커밋하지 않는다.
- `prompts/` — LLM 프롬프트 템플릿(분류, 요약, 리스크 판정 등). 파일명은 `<use_case>.md` 규칙.
- `scripts/` — 실행 가능한 파이썬 스크립트. API 클라이언트, ETL, 리포트 생성기가 위치한다.
- `reports/` — 스크립트가 생성한 산출물(Markdown/HTML/CSV). 날짜·작업 단위 하위 폴더를 권장.

## UNIPASS API

- 엔드포인트: `https://unipass.customs.go.kr:38010/ext/rest/<serviceName>/<operation>`
- 인증키(crkyCn)는 환경변수 `UNIPASS_API_KEY` 로만 주입한다. 코드/로그에 평문으로 남기지 않는다.
- 기본 클라이언트: `scripts/unipass_client.py` 의 `UnipassClient` 를 재사용한다.
- 확인된 주요 서비스명:
  - HS 부호 조회: `hsSgnQry/searchHsSgn`
  - 관세환율 정보: `trifFxrtInfoQry/retrieveTrifFxrtInfo`
  - 통계부호: `statsSgnQry/retrieveStatsSgnBrkd`
  - 화물통관 진행: `cargCsclPrgsInfoQry/retrieveCargCsclPrgsInfo`
- **관세율표(trrf) 와 품목분류 사례는 UNIPASS API 미제공** → CLIP 스크래퍼로 수집한다.
- 응답 XML 태그명은 로그인 후 연계가이드 PDF 에만 공개되므로, 신규 서비스 연동 시
  실 호출로 구조를 확인한 뒤 파서를 확정한다.

## Conventions

- Python 3.10+ 기준. 외부 호출은 `requests` 사용, 응답은 XML 이 기본이므로 `xml.etree.ElementTree` 로 파싱.
- 비밀값은 `.env` 또는 쉘 환경변수로 관리하며 저장소에 커밋 금지.
- 신규 스크립트는 `python -m scripts.<name>` 형태로 실행 가능하도록 작성.

## Branching

작업 브랜치는 `claude/<topic>-<slug>` 규칙을 사용하고, 기본 개발 브랜치는 별도 지정 시 해당 브랜치로 푸시한다.
