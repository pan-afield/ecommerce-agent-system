export function getBackendDetail(value: unknown): string | null {
  if (
    typeof value !== "object" ||
    value === null ||
    !("detail" in value) ||
    typeof value.detail !== "string" ||
    value.detail.length === 0
  ) {
    return null;
  }

  return value.detail;
}
