"""수집 데이터 영속화 및 RAG 전처리 도우미."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

try:
    import tiktoken  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    tiktoken = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

DEFAULT_ITEM_MASTER = Path("data/item_master.csv")
ITEM_MASTER_COLUMNS = ["hs_code", "name_kr", "name_en", "base_rate", "source"]

DEFAULT_CHUNK_TOKENS = 800
DEFAULT_CHUNK_OVERLAP = 100
DEFAULT_ENCODING = "cl100k_base"

_ENCODER_CACHE: dict[str, object] = {}


@dataclass
class DataManager:
    """CSV 마스터 관리 + 벡터 DB 적재용 청크 변환."""

    item_master_path: Path = DEFAULT_ITEM_MASTER

    def upsert_items(self, items: Iterable[Mapping[str, object]]) -> int:
        """``hs_code`` 를 키로 신규/갱신 레코드를 병합 저장.

        :returns: 변경(추가 또는 업데이트)된 행 수.
        """
        new_df = pd.DataFrame(list(items))
        if new_df.empty:
            return 0
        if "hs_code" not in new_df.columns:
            raise ValueError("items 에 'hs_code' 컬럼이 필요합니다.")

        existing = self._load_master()
        merged = pd.concat([existing, new_df], ignore_index=True)
        merged = merged.drop_duplicates(subset=["hs_code"], keep="last")

        for col in ITEM_MASTER_COLUMNS:
            if col not in merged.columns:
                merged[col] = pd.NA
        merged = merged[ITEM_MASTER_COLUMNS]

        self.item_master_path.parent.mkdir(parents=True, exist_ok=True)
        merged.to_csv(self.item_master_path, index=False)

        changed = len(merged) - len(existing)
        logger.info("item_master upsert: 추가=%s, 총=%s", max(changed, 0), len(merged))
        return max(changed, 0)

    def _load_master(self) -> pd.DataFrame:
        if not self.item_master_path.exists():
            return pd.DataFrame(columns=ITEM_MASTER_COLUMNS)
        return pd.read_csv(self.item_master_path, dtype={"hs_code": str})

    @staticmethod
    def chunk_explanatory_note(
        note: Mapping[str, object],
        chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
        overlap_tokens: int = DEFAULT_CHUNK_OVERLAP,
        encoding: str | None = DEFAULT_ENCODING,
    ) -> list[dict[str, object]]:
        """해설서 한 건을 부/류 주 + 본문으로 나누고 토큰 단위로 청킹.

        :param encoding: ``tiktoken`` BPE 인코딩명 (기본 ``cl100k_base``).
            ``None`` 지정 시 공백 분리 근사치 사용. tiktoken 이 미설치이거나
            실패하면 자동으로 공백 분리 fallback.
        """
        heading = str(note.get("heading", ""))
        metadata_base = dict(note.get("metadata") or {})
        metadata_base.setdefault("heading", heading)

        parts: list[tuple[str, str]] = []
        for kind in ("section_note", "chapter_note", "content"):
            text = note.get(kind)
            if isinstance(text, str) and text.strip():
                parts.append((kind, text.strip()))

        chunks: list[dict[str, object]] = []
        for kind, text in parts:
            for idx, piece in enumerate(
                _split_with_overlap(text, chunk_tokens, overlap_tokens, encoding)
            ):
                chunks.append(
                    {
                        "text": f"[{heading}/{kind}] {piece}",
                        "metadata": {
                            **metadata_base,
                            "kind": kind,
                            "chunk_index": idx,
                        },
                    }
                )
        return chunks


_TOKEN_PATTERN = re.compile(r"\S+")


def _get_encoder(name: str):
    if tiktoken is None:
        return None
    if name not in _ENCODER_CACHE:
        _ENCODER_CACHE[name] = tiktoken.get_encoding(name)
    return _ENCODER_CACHE[name]


def _split_with_overlap(
    text: str,
    chunk_tokens: int,
    overlap_tokens: int,
    encoding: str | None = DEFAULT_ENCODING,
) -> list[str]:
    if chunk_tokens <= 0:
        raise ValueError("chunk_tokens 는 1 이상이어야 합니다.")
    if overlap_tokens < 0 or overlap_tokens >= chunk_tokens:
        raise ValueError("overlap_tokens 는 0 이상 chunk_tokens 미만이어야 합니다.")

    if encoding:
        try:
            enc = _get_encoder(encoding)
        except Exception as exc:  # noqa: BLE001
            logger.warning("tiktoken 인코더 로드 실패(%s), 공백 분리 fallback: %s", encoding, exc)
            enc = None
        if enc is not None:
            return _split_tokens_tiktoken(text, chunk_tokens, overlap_tokens, enc)

    return _split_tokens_whitespace(text, chunk_tokens, overlap_tokens)


def _split_tokens_tiktoken(text: str, chunk_tokens: int, overlap_tokens: int, enc) -> list[str]:
    ids = enc.encode(text)
    if not ids:
        return []
    step = chunk_tokens - overlap_tokens
    chunks: list[str] = []
    for start in range(0, len(ids), step):
        window = ids[start : start + chunk_tokens]
        if not window:
            break
        chunks.append(enc.decode(window))
        if start + chunk_tokens >= len(ids):
            break
    return chunks


def _split_tokens_whitespace(text: str, chunk_tokens: int, overlap_tokens: int) -> list[str]:
    tokens = _TOKEN_PATTERN.findall(text)
    if not tokens:
        return []
    step = chunk_tokens - overlap_tokens
    chunks: list[str] = []
    for start in range(0, len(tokens), step):
        window = tokens[start : start + chunk_tokens]
        if not window:
            break
        chunks.append(" ".join(window))
        if start + chunk_tokens >= len(tokens):
            break
    return chunks
