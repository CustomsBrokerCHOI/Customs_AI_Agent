#!/usr/bin/env bash
# HSK 전체 적재 체인 — Stage 1 → 2 → 3 → 4.
#
# 각 stage 는 idempotent/재개 가능. 중간에 중단/실패 후 재실행 안전.
# 총 예상시간 6-7시간 + OpenAI 임베딩 ~$2.
#
# 사용:
#   bash scripts/run_full_hsk.sh                 # 전체
#   bash scripts/run_full_hsk.sh 2>&1 | tee data/usage/full_hsk.log  # 로그 병행
#
# 경고: build_tariff_rates / build_explanatory_notes 가 이미 다른 프로세스에서
# 실행 중이면 절대 동시 실행하지 말 것 (Playwright 충돌 + CLIP 서버 부하).

set -euo pipefail

cd "$(dirname "$0")/.."   # repo root

log() {
  echo
  echo "==============================================="
  echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*"
  echo "==============================================="
}

log "Stage 1/4 — CLIP 관세율표 전체 스캔 (5~6h, 재개 가능)"
python -u -m scripts.build_tariff_rates --chapters 01-97 --skip-chapters 77

log "Stage 2/4 — 관세율표 캐시 → DB (hs_codes + tariff_rates)"
python -u -m scripts.build_index tariffs

log "Stage 3a/4 — CLIP 해설서 전체 스캔 (3~4h, 재개 가능)"
python -u -m scripts.build_explanatory_notes

log "Stage 3b/4 — 해설서 캐시 → DB (explanatory_notes + note_chunks)"
python -u -m scripts.build_index notes "data/cache/notes/*.json"

log "Stage 4/4 — OpenAI 임베딩 (text-embedding-3-large, 1536d, ~30min, ~\$2)"
python -u -m scripts.build_embeddings notes --yes

log "✅ 전체 파이프라인 완료"
