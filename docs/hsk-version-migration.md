# HSK 버전 개정 대응 런북

HS 품목분류표(HSK)는 세계관세기구(WCO) 주관으로 **5년 주기** 개정된다
(2012 → 2017 → 2022 → 2027 예정). 본 문서는 신 HSK 공포 후 우리 시스템이
**무중단으로 듀얼 운영 → 완전 전환** 하는 절차를 정의한다.

---

## 1. 감지

### 자동 프로브

```bash
python -m scripts.detect_hsk_version
```

- CLIP 해설서 페이지의 연도 드롭다운 관측값을 DB ``explanatory_notes.hsk_year``
  최대값과 비교.
- 신버전 감지 시 **exit 2** + stderr 경보. cron/GitHub Actions 훅 가능.
- CLIP 접근 불가 시 ``--skip-clip`` 로 DB 만 확인.

### 수동 트리거

- 기획재정부 고시·관세청 뉴스로 먼저 공지되는 경우가 다수. 발효일 **3개월 전**
  런북 시작 권장 (스크래핑 + 재임베딩 소요 시간 확보).

---

## 2. 듀얼 운영 기간

- **공포일 ~ 발효일 전**: 기존 `hsk_year=YYYY` 로 모든 신고·분류 유지.
- **발효일 당일부터**: 신규 `ClassifyJob.hsk_year=YYYY+5` 로 자동 주입.
  수입신고 기준일이 발효일 이후이면 신버전 조회.
- **발효일 ~ +90일**: 과도기. 이전 신고 재검토용으로 양버전 모두 쿼리 가능.
- **+90일 이후**: 구버전 deprecate. 재검토 요청은 별도 플래그로만 허용.

---

## 3. 단계별 마이그레이션

### 3.1. 신 해설서 스크래핑

```bash
# 예: 2027 년 HSK 로 가정
export NEW_YEAR=2027
# 해설서 21개 부(Section) 대표 호로 커버리지 확장
python -m scripts.dev_probe_clip --year $NEW_YEAR 8471
# ... 각 호별 반복 (배치 스크립트는 후속)
```

산출물: ``data/cache/clip_note_<heading>_<year>.json``.

### 3.2. DB 적재

```bash
# 해설서 JSONL → explanatory_notes + note_chunks (embedding=NULL)
python -m scripts.build_index notes 'data/cache/clip_note_*_2027_*.json'
```

같은 heading 이라도 ``(heading, kind, lang, hsk_year)`` 유니크 키로 **신/구 양립**.

### 3.3. 품목분류 사례 재수집 (선택)

개정 시 사례도 신 HS 체계로 재분류되는 경우가 있음. 주요 검색어 배치로 재수집.

```bash
python -m scripts.dev_probe_cases 노트북
python -m scripts.dev_probe_cases 8471
```

### 3.4. 재임베딩

```bash
# 신 버전 note_chunks 만 대상 (embedding IS NULL)
python -m scripts.build_embeddings notes --dry-run    # 비용 견적
python -m scripts.build_embeddings notes --yes        # 실행
python -m scripts.build_embeddings cases --yes         # 사례 재임베딩
```

`EmbeddingVersion` 은 기존 active 를 **유지** 해도 무방 (모델 자체는 동일).
모델 교체가 동시 진행되면 ``--force`` 로 전체 재생성.

### 3.5. 엔진 hsk_year 라우팅

코드 변경 불필요 — `ClassifyJob.hsk_year` 가 자동으로 `ClassifyInput.hsk_year`
→ `fetch_note_bundle(..., hsk_year=...)` 로 전파됨 (2026-04-22 구현).

**라우터에서 신 연도 기본값 전환** (발효일 이후):

```python
# api/routers/classify.py::create_classify_job 내부
NEW_HSK_YEAR = 2027  # 발효일 이후 전환
job = ClassifyJob(
    user_id=user.id,
    product_name=payload.product_name,
    description=payload.description,
    image_url=payload.image_url,
    status="pending",
    hsk_year=NEW_HSK_YEAR,  # 신 기본값
)
```

또는 환경변수 `ACTIVE_HSK_YEAR` 도입 후 `settings.active_hsk_year` 로 참조.

### 3.6. 검증

- 듀얼 평가셋 빌드:
  ```bash
  python -m scripts.build_eval_set --out data/eval_set_2027.jsonl  # 신규
  ```
- 신 버전 Recall@5/MRR 측정:
  ```bash
  python -m scripts.eval_search --eval-set data/eval_set_2027.jsonl
  ```
- 구 버전 대비 **회귀 없음** 확인 (Recall@5 감소 5% 이내 권장).

### 3.7. 프런트엔드 표기

- `/classify/new` 폼에 "적용 HSK 연도" 드롭다운 노출 (후속 UI 작업).
- 분류의견서 PDF 머리말에 `HSK ${hsk_year}` 명시 (이미 engine 단계에서 meta 포함).

---

## 4. Deprecation (+90일 이후)

### 4.1. 신규 요청 차단

```python
if payload_hsk_year < ACTIVE_HSK_YEAR:
    raise HTTPException(410, "구 HSK 버전은 신규 요청 대상이 아닙니다. 관리자 문의.")
```

### 4.2. DB 정리 (**삭제 금지**)

- 기존 `hsk_year` 레코드는 **보존** (재검토 요청·법적 증빙).
- `note_chunks.embedding_version` 을 신 버전으로 재지정해 검색 기본 경로에서 제외.
- 파티셔닝 도입 시 `hsk_year=OLD` 파티션을 read-only 로 분리.

---

## 5. 롤백 시나리오

신 버전 전환 후 심각한 회귀 (Recall 급락 등) 시:

1. 라우터 `ACTIVE_HSK_YEAR` 를 구 값으로 복구.
2. 신 버전으로 이미 생성된 `ClassifyJob` 은 사용자 안내 후 재요청 권고
   (``hsk_year`` 는 immutable 정책 유지).
3. `audit_logs` 에 `classify.hsk_rollback` 액션 추가해 추적.

---

## 6. 소요·비용 추산 (2027 가정)

| 항목 | 예상 |
|---|---|
| 해설서 전체 스크래핑 (21 부 × 대표 호) | ~300 호 × 2초 = **10분** |
| 적재 (`build_index notes`) | ~1000 청크 × 100ms = **2분** |
| 재임베딩 (OpenAI text-embedding-3-large) | ~1M 토큰 × $0.13/1M = **$0.13** |
| 평가셋 재구성·Recall 측정 | 수기 라벨 5건 + 자동 45건 = **반나절** |
| 전체 런북 완료 | **1~2 근무일** |

---

## 7. 체크리스트

- [ ] `detect_hsk_version.py` 가 exit 2 로 신버전 감지
- [ ] 신 해설서 스크래핑 완료 (manifest 확인)
- [ ] `build_index notes` 로 `note_chunks.embedding IS NULL` 상태 적재
- [ ] `build_embeddings notes` 완료, `note_chunks.embedding IS NOT NULL` 확인
- [ ] 신 평가셋 Recall@5 / MRR 측정 + 구버전 대비 기록
- [ ] 라우터 `ACTIVE_HSK_YEAR` 전환 배포
- [ ] 관세사 공지 (대시보드 배너 / 이메일)
- [ ] +90일 후 deprecation 플래그 적용
- [ ] 런북 후기 문서 (`docs/hsk-migration-YYYY.md`)
