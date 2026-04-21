# CLIP 스크래핑 운영 정책

대상 사이트: **관세청 CLIP (관세법령정보포털)** — `https://unipass.customs.go.kr/clip/`.
수집 스크래퍼: `scripts/clip_scraper.py` (Playwright 기반).

본 정책은 ① 우리 기본 동작 ② 근거 확인 절차 ③ 침해 시 대응을 기록한다.
변경 시 이 문서를 먼저 갱신하고 PR 을 연다.

---

## 1. 우리 기본 동작

| 항목 | 값 | 위치 |
|---|---|---|
| User-Agent | `CustomsAIAgent/0.1 (+contact: ops@example.com)` | `DEFAULT_USER_AGENT` (`clip_scraper.py`) |
| Rate limit (요청 간격) | 최소 1초 | `DEFAULT_RATE_LIMIT_SEC` + `_respect_rate_limit` |
| 동시성 | 단일 브라우저 컨텍스트 · 단일 탭 | `__enter__` |
| 재시도 | transient 오류에 대해 지수 백오프 3회 | `_retry_transient()` |
| 저장 경로 | 원본 HTML: `data/raw/clip/<subdir>/<stem>.html`, 메타: `data/manifest.jsonl` | `_persist_raw_html` / `_append_manifest` |
| 비공개 데이터 | 로그인 필요 자원 수집 안 함 (관세사 로그인 storage_state 미사용) | — |
| CAPTCHA | 우회 시도 안 함 — 차단 시 즉시 중단 | 운영 규칙 |

### 스크래핑 대상 엔드포인트

- `/clip/hsinfosrch/openULS0202001Q.do` — **HS 해설서** (통칙/부주/류주/호주)
- `/clip/hsinfosrch/openULS0201002Q.do` — **관세율표** (UNIPASS `retrieve_trrt` 의 보조)
- `/clip/hsinfosrch/openULS0203042S.do` — **품목분류 사례** (PRD 핵심 데이터)

**수집 범위 밖**:
- 관세청 로그인이 필요한 모든 리소스
- 관세사 회원 전용 게시판
- 외부 링크로 나가는 콘텐츠 (WCO 영문 해설서 원문 등)

---

## 2. robots.txt 확인 절차

관세청 사이트 robots.txt 는 시간에 따라 변경될 수 있다. 다음 절차로 **분기마다** 재확인한다.

### 2.1. 자동 프로브

```bash
python -m scripts.dev_probe_robots
```

- `https://unipass.customs.go.kr/robots.txt` 를 가져와 `data/raw/clip/robots/YYYYMMDD.txt` 에 저장.
- 이전 프로브와 diff 를 출력 — 변경 있으면 정책 재검토.

### 2.2. 현재까지 확인된 지침

> **마지막 확인일**: 개발자가 `scripts/dev_probe_robots.py` 를 실행 후 날짜/내용을 여기에 기록한다.
>
> 예 (템플릿, 실제 확인 후 교체):
> - `/clip/` 경로에 대한 Disallow **없음** 확인 (YYYY-MM-DD)
> - crawl-delay **없음** — 우리 내부 1초 rate limit 유지

**현재 상태**: 프로브 미실행. 상업 배포 전 필수 실행.

### 2.3. robots.txt 준수 규칙

- `Disallow: /clip/hsinfosrch/...` 규칙이 생기면 해당 엔드포인트 수집 즉시 **중단**.
- `crawl-delay: N` 지시가 있으면 `DEFAULT_RATE_LIMIT_SEC` 을 그 값 이상으로 설정.
- `User-agent: *` 보다 `User-agent: CustomsAIAgent` 매치가 있으면 후자 우선.

---

## 3. 이용약관 (관세청 포털)

> **마지막 검토일**: 개발자가 포털의 이용약관/저작권 정책 페이지를 확인 후 여기에
> 요약을 기재한다.
>
> 현재까지 알려진 원칙 (공공 정보 일반론):
> - 관세청 공시 법령·고시·통칙·해설서는 **공공데이터 포털 정책에 따라 2차 가공·재배포 가능** (출처 명시).
> - 품목분류 사례는 결정서의 공개본 — 개인정보 없음. 출처 `관세청 CLIP 포털` 명시.
> - 수집 서비스가 **유상 서비스** 로 제공될 경우 별도 검토 필요. 현재 MVP 는 관세사 보조 무상/제한적 유상이므로 일반 공개 범위 내.

**결정 보류**: 유상 SaaS 전환 시 관세청에 공식 문의 또는 법률 자문 필요.

---

## 4. 수집 중 오류 대응

### 4.1. 실패 유형별 동작

| 실패 | 대응 |
|---|---|
| Playwright `TimeoutError` (셀렉터 대기) | `_retry_transient` 가 지수 백오프 3회 재시도. 최종 실패 시 `ClipScrapeError` raise + `manifest.jsonl` 에 `event: "failure"` 기록 |
| HTTP 429/503 | rate_limit 을 2배 늘리고 재시도 (수동) |
| HTTP 4xx (403 등) | 즉시 중단. robots.txt / TOS 재확인 |
| CAPTCHA 등장 | 즉시 중단. 수집 빈도 조정 + 관세청 연락 |

### 4.2. Manifest 포맷 (`data/manifest.jsonl`)

```jsonl
{"fetched_at": "2026-04-22T10:00:00Z", "event": "success", "kind": "explanatory_note", "heading": "8471", "year": "2022", "path": "data/raw/clip/explanatory_note/8471_2022.html"}
{"fetched_at": "2026-04-22T10:00:05Z", "event": "failure", "kind": "explanatory_note", "heading": "9999", "year": "2022", "error": "ClipScrapeError: 결과 테이블 로딩 실패"}
```

- 실패 로그도 append 하여 **재수집 배치 시 skip/retry** 판단 재료로 활용.

---

## 5. 개인정보·민감정보

- CLIP 페이지 구조상 개인정보가 포함된 케이스는 없으나, 품목분류 사례 본문에
  기업명·신청자 정보가 **부분 마스킹되지 않은 채** 공개된 경우가 드물게 있다.
- 수집 후 `reasoning` 본문에서 **주민등록번호/사업자등록번호/개인 연락처** 패턴이
  감지되면 즉시 해당 레코드를 로컬에서도 삭제. 정규식 기반 사후 필터는
  `scripts/redact_pii.py` (미구현, 필요 시 작성).

---

## 6. 법적 연락처

- 관세청 정보공개담당: 044-204-xxxx (운영 시 정확한 번호로 교체)
- 사이트 관련 문의: 포털 하단 "문의" 메뉴
- 우리 쪽 운영자: `ops@example.com` (교체)

침해 의심 연락을 받으면 **해당 수집 즉시 중단** 하고 24시간 내 서면 회신.
