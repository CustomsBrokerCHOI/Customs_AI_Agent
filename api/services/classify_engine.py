"""분류 엔진 — Week 4-5 에 실구현.

Approach B (Top-3 wedge) 5단계 알고리즘:
  3-A  물품 식별 (Input Gate)  — LLM 구조화 (재질·용도·기능)
  3-B  부 결정 + pgvector 검색  — Top-K 후보
  3-C  부-류 일치 검증 (Verification Gate)
  3-D  호/주/해설서 RAG 검증   — DB 원문 직접 주입 (할루시네이션 방지)
  3-E  불일치 → Retry Loop

현재 파일은 **플레이스홀더**. run() 은 TODO, 실제로는 mock_result() 로 대체 동작.
Eng Review 결정 반영: Top-3 → asyncio.gather 병렬 검증, 프롬프트 인젝션 샌드박스,
임베딩 버전 mismatch 감지는 실구현 시 추가.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ClassifyInput:
    product_name: str
    description: str
    image_url: str | None = None


@dataclass
class CandidateOut:
    rank: int
    hs_code: str
    name_kr: str | None
    name_en: str | None
    heading: str
    sub_heading: str
    breadcrumb: list[str]
    confidence: float
    base_tariff_rate: str | None
    verified: bool
    citations: list[dict]


@dataclass
class EngineResult:
    candidates: list[CandidateOut]
    notice: str | None = None
    meta: dict = field(default_factory=dict)


def mock_result(product_name: str) -> EngineResult:
    """실엔진 연결 전까지 UI·폴링 플로우 검증용 가짜 결과."""
    return EngineResult(
        candidates=[
            CandidateOut(
                rank=1,
                hs_code="8471300000",
                name_kr="휴대용 자동자료처리기계(중량 10kg 이하...)",
                name_en="Portable automatic data processing machines...",
                heading="8471",
                sub_heading="30",
                breadcrumb=[
                    "제16부 기계류·전기기기",
                    "제84류 원자로·보일러·기계류",
                    "제8471호 자동자료처리기계",
                    "8471.30 휴대용",
                ],
                confidence=0.94,
                base_tariff_rate="8",
                verified=True,
                citations=[
                    {
                        "source_kind": "heading_note",
                        "heading": "8471",
                        "excerpt": "휴대용 자동자료처리기계란 중량 10kg 이하이며 적어도 CPU, 키보드, 디스플레이를 갖춘 기계를 말한다.",
                    }
                ],
            ),
        ],
        notice=f"[STUB] 실엔진 미연결 — mock 결과 (입력: {product_name[:50]})",
        meta={"engine": "stub"},
    )


# TODO (Week 4-5): 실구현
# async def run(inp: ClassifyInput, db, llm_client, embed_client) -> EngineResult:
#     features = await extract_features_llm(inp, llm_client)           # 3-A
#     candidates = await search_candidates_pgvector(features, db)      # 3-B
#     filtered = verify_section_chapter(candidates, features)          # 3-C
#     verified = await asyncio.gather(*[                                # 3-D
#         rag_verify(c, features, db, llm_client) for c in filtered[:3]
#     ], return_exceptions=True)
#     return build_result(verified)                                     # 3-E
