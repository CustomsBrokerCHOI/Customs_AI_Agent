"""공용 pytest 설정 + fixture.

모든 테스트에서 `.env` 나 셸 환경변수를 무시하고 동결된 키를 주입한다.
실제 UNIPASS 호출은 일어나지 않도록 `UnipassClient.call` 을 mock 하는 쪽에서 처리한다.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """각 테스트가 UNIPASS_API_KEY_* 환경변수 오염에서 자유롭도록."""
    for k in list(os.environ):
        if k.startswith("UNIPASS_API_KEY"):
            monkeypatch.delenv(k, raising=False)
