"""DataManager 단위 테스트."""

from __future__ import annotations

import pandas as pd
import pytest

from scripts.data_manager import (
    DataManager,
    _get_encoder,
    _split_tokens_tiktoken,
    _split_tokens_whitespace,
    _split_with_overlap,
)


def test_upsert_items_adds_new_rows(tmp_path) -> None:
    manager = DataManager(item_master_path=tmp_path / "item_master.csv")
    changed = manager.upsert_items(
        [
            {"hs_code": "8471300000", "name_kr": "노트북", "source": "UNIPASS"},
            {"hs_code": "2203000000", "name_kr": "맥주", "source": "UNIPASS"},
        ]
    )
    assert changed == 2
    df = pd.read_csv(manager.item_master_path, dtype={"hs_code": str})
    assert set(df["hs_code"]) == {"8471300000", "2203000000"}


def test_upsert_items_dedup_keeps_last(tmp_path) -> None:
    manager = DataManager(item_master_path=tmp_path / "item_master.csv")
    manager.upsert_items([{"hs_code": "1", "name_kr": "old", "source": "A"}])
    manager.upsert_items([{"hs_code": "1", "name_kr": "new", "source": "B"}])
    df = pd.read_csv(manager.item_master_path, dtype={"hs_code": str})
    assert len(df) == 1
    assert df.iloc[0]["name_kr"] == "new"
    assert df.iloc[0]["source"] == "B"


def test_upsert_items_requires_hs_code(tmp_path) -> None:
    manager = DataManager(item_master_path=tmp_path / "item_master.csv")
    with pytest.raises(ValueError, match="hs_code"):
        manager.upsert_items([{"name_kr": "no_id"}])


def test_upsert_items_empty_is_noop(tmp_path) -> None:
    manager = DataManager(item_master_path=tmp_path / "item_master.csv")
    assert manager.upsert_items([]) == 0
    assert not manager.item_master_path.exists()


def test_chunk_explanatory_note_tiktoken(tmp_path) -> None:
    long_text = "관세 분류는 HS 부호 체계에 따라 수행된다. " * 50
    chunks = DataManager.chunk_explanatory_note(
        {
            "heading": "8471",
            "content": long_text,
            "metadata": {"lang": "ko"},
        },
        chunk_tokens=100,
        overlap_tokens=20,
    )
    assert len(chunks) >= 2
    assert all(c["metadata"]["heading"] == "8471" for c in chunks)
    assert all(c["metadata"]["kind"] == "content" for c in chunks)
    # chunk_index 는 0부터 순차
    assert [c["metadata"]["chunk_index"] for c in chunks] == list(range(len(chunks)))


def test_chunk_explanatory_note_whitespace_fallback() -> None:
    text = "word " * 500
    chunks = DataManager.chunk_explanatory_note(
        {"heading": "0000", "content": text.strip()},
        chunk_tokens=100,
        overlap_tokens=20,
        encoding=None,  # tiktoken 강제 우회
    )
    # 500 토큰 / (100-20) = 7 청크 (마지막 포함)
    assert 5 <= len(chunks) <= 10


def test_split_tokens_whitespace_overlap() -> None:
    text = " ".join(str(i) for i in range(200))
    chunks = _split_tokens_whitespace(text, chunk_tokens=50, overlap_tokens=10)
    # 200 tokens, step=40 → starts at 0,40,80,120,160
    assert len(chunks) == 5
    # 오버랩 확인: 첫 청크의 끝 부분이 두 번째 청크의 시작 부분에 포함
    first_end = chunks[0].split()[-10:]
    second_start = chunks[1].split()[:10]
    assert first_end == second_start


def test_split_tokens_tiktoken_returns_decoded_strings() -> None:
    enc = _get_encoder("cl100k_base")
    assert enc is not None
    text = "Hello, this is a test. " * 20
    chunks = _split_tokens_tiktoken(text, chunk_tokens=20, overlap_tokens=5, enc=enc)
    assert len(chunks) >= 2
    assert all(isinstance(c, str) for c in chunks)
    assert all(c.strip() for c in chunks)


def test_split_with_invalid_overlap_raises() -> None:
    with pytest.raises(ValueError, match="overlap_tokens"):
        _split_with_overlap("a b c", chunk_tokens=10, overlap_tokens=10)


def test_split_with_zero_chunk_tokens_raises() -> None:
    with pytest.raises(ValueError, match="chunk_tokens"):
        _split_with_overlap("a b c", chunk_tokens=0, overlap_tokens=0)
