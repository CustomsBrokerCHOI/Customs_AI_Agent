// HS CODE 표시 포맷 유틸. 저장값은 10자리 raw, 표시값만 XXXX.XX-XXXX 로 포맷.

export function formatHSCode(code: string | null | undefined): string {
  if (!code) return "";
  const trimmed = String(code).trim().replace(/[.\s-]/g, "");
  if (trimmed.length === 10 && /^\d{10}$/.test(trimmed)) {
    return `${trimmed.slice(0, 4)}.${trimmed.slice(4, 6)}-${trimmed.slice(6, 10)}`;
  }
  // 6자리(소호)까지만 있는 경우 XXXX.XX 로 표기
  if (trimmed.length === 6 && /^\d{6}$/.test(trimmed)) {
    return `${trimmed.slice(0, 4)}.${trimmed.slice(4, 6)}`;
  }
  // 4자리(호)는 그대로 (점 없음)
  return String(code).trim();
}
