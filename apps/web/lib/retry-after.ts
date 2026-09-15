export const DEFAULT_RATE_LIMIT_RETRY_AFTER_SECONDS = 60;

const MAX_RETRY_AFTER_SECONDS = 86_400;

export function parseRetryAfterSeconds(value: string | null): number | undefined {
  const normalized = value?.trim() ?? "";
  if (!/^[1-9]\d*$/.test(normalized)) {
    return undefined;
  }

  const seconds = Number(normalized);
  return Number.isSafeInteger(seconds) && seconds <= MAX_RETRY_AFTER_SECONDS
    ? seconds
    : undefined;
}

export function formatRateLimitMessage(seconds: number) {
  return `请求过于频繁，请在 ${seconds} 秒后重试。`;
}
