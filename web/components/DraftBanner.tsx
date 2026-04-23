// 모든 분류 결과는 Draft 상태임을 UI 상단에 상시 노출한다.
// CLAUDE.md: "UI·PDF·API 응답에서 이 면책을 항상 명시한다."
export function DraftBanner() {
  return (
    <div
      role="note"
      className="border border-draft-border bg-draft-bg text-draft-text text-sm px-4 py-2 rounded-md"
    >
      <strong className="font-semibold">Draft</strong> — 본 도구의 분류 결과는 초안입니다.
      관세사의 최종 확인없이 활용하지 마시기 바랍니다.
    </div>
  );
}
