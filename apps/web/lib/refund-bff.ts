import { NextResponse } from "next/server";

import { getBackendDetail } from "@/lib/backend-error";
import { getSessionAgentCoreAuthorization } from "@/lib/server-auth";
import type { RefundError, RefundErrorCode, RefundReasonCode } from "@/types/refund";

const DEFAULT_AGENT_CORE_URL = "http://localhost:8000";
const REFUND_UPSTREAM_TIMEOUT_MS = 8_000;
const UNSAFE_ID_CHARACTERS = "/\\?#";

const reasonMessages: Record<RefundReasonCode, string> = {
  amount_exceeds_order_total: "退款金额不能超过订单金额。",
  currency_mismatch: "退款币种必须与订单币种一致。",
  eligible_for_review: "退款申请可以进入确认流程。",
  invalid_amount: "退款金额必须大于 0。",
  order_not_owned: "订单不存在。",
  order_not_refundable: "当前订单状态不支持退款申请。",
};

export function refundErrorResponse(
  status: number,
  code: RefundErrorCode,
  message: string,
) {
  return NextResponse.json<RefundError>({ error: { code, message } }, { status });
}

export function isSafeRefundId(value: string, maxLength = 128) {
  return (
    value.length > 0 &&
    Array.from(value).length <= maxLength &&
    !Array.from(value).some((character) => {
      const codePoint = character.codePointAt(0) ?? 0;
      return codePoint <= 31 || codePoint === 127 || UNSAFE_ID_CHARACTERS.includes(character);
    })
  );
}

export function isRefundAmount(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length <= 32 &&
    /^-?\d+(?:\.\d+)?$/.test(value)
  );
}

export function isRefundCurrency(value: unknown): value is string {
  return typeof value === "string" && /^[A-Za-z]{3}$/.test(value);
}

interface ProxyRefundOptions {
  body?: unknown;
  isSuccessBody?: (value: unknown) => boolean;
  method: "GET" | "POST";
  path: string;
  successStatus?: 200 | 204;
  unknownOutcomeOnTimeout?: boolean;
}

function isTimeoutError(error: unknown) {
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    (error.name === "AbortError" || error.name === "TimeoutError")
  );
}

export async function proxyRefundRequest(options: ProxyRefundOptions) {
  const authorization = await getSessionAgentCoreAuthorization();
  if (authorization === null) {
    return refundErrorResponse(
      401,
      "refund_unauthorized",
      "请先登录。",
    );
  }

  const agentCoreUrl = (process.env.AGENT_CORE_URL || DEFAULT_AGENT_CORE_URL).replace(/\/+$/, "");
  let upstreamResponse: Response;
  try {
    upstreamResponse = await fetch(`${agentCoreUrl}${options.path}`, {
      method: options.method,
      headers: options.body === undefined
        ? { authorization }
        : { authorization, "content-type": "application/json" },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      cache: "no-store",
      signal: AbortSignal.timeout(REFUND_UPSTREAM_TIMEOUT_MS),
    });
  } catch (error) {
    const timedOut = isTimeoutError(error);
    return refundErrorResponse(
      timedOut ? 504 : 503,
      timedOut && options.unknownOutcomeOnTimeout
        ? "refund_outcome_unknown"
        : timedOut
          ? "refund_upstream_timeout"
          : "refund_upstream_unreachable",
      timedOut
        ? options.unknownOutcomeOnTimeout
          ? "退款结果暂时未知，请查询执行状态。"
          : "退款服务响应超时，请稍后重试。"
        : "无法连接退款服务，请稍后重试。",
    );
  }

  if (upstreamResponse.status === 204 && options.successStatus === 204) {
    return new NextResponse(null, { status: 204 });
  }

  let upstreamBody: unknown;
  try {
    upstreamBody = await upstreamResponse.json();
  } catch {
    return refundErrorResponse(
      502,
      "refund_invalid_upstream_response",
      "退款服务返回了无效响应，请稍后重试。",
    );
  }

  if (upstreamResponse.ok && options.isSuccessBody?.(upstreamBody)) {
    return NextResponse.json(upstreamBody, { status: upstreamResponse.status });
  }

  const detail = getBackendDetail(upstreamBody);
  if (upstreamResponse.status === 401 && detail) {
    return refundErrorResponse(401, "refund_unauthorized", "登录状态无效，请重新登录。");
  }
  if (upstreamResponse.status === 403 && detail) {
    return refundErrorResponse(403, "refund_forbidden", "当前身份无权执行此退款操作。");
  }
  if (upstreamResponse.status === 429) {
    const response = refundErrorResponse(429, "refund_rate_limited", "操作过于频繁，请稍后重试。");
    const retryAfter = upstreamResponse.headers.get("retry-after");
    if (retryAfter) response.headers.set("retry-after", retryAfter);
    return response;
  }
  if (upstreamResponse.status === 404 && detail) {
    return refundErrorResponse(404, "refund_not_found", detail);
  }
  if (upstreamResponse.status === 409 && detail) {
    return refundErrorResponse(409, "refund_conflict", detail);
  }
  if (
    upstreamResponse.status === 422 &&
    typeof detail === "string" &&
    detail in reasonMessages
  ) {
    return refundErrorResponse(
      422,
      "refund_ineligible",
      reasonMessages[detail as RefundReasonCode],
    );
  }
  if (upstreamResponse.status === 422) {
    return refundErrorResponse(400, "refund_invalid_request", "退款请求格式无效。");
  }
  if (upstreamResponse.status === 503 && detail === "认证服务尚未配置。") {
    return refundErrorResponse(503, "refund_auth_unavailable", "认证服务尚未配置，请联系管理员。");
  }
  if (upstreamResponse.status === 503 && detail === "退款审批服务尚未配置。") {
    return refundErrorResponse(
      503,
      "refund_approver_unavailable",
      "退款审批服务尚未配置，请联系管理员。",
    );
  }
  if (upstreamResponse.status === 503 && detail) {
    if (
      options.unknownOutcomeOnTimeout &&
      detail === "退款结果暂时未知，请稍后查询。"
    ) {
      return refundErrorResponse(503, "refund_outcome_unknown", detail);
    }
    return refundErrorResponse(503, "refund_service_unavailable", "退款服务暂时不可用，请稍后重试。");
  }

  return refundErrorResponse(
    502,
    "refund_invalid_upstream_response",
    "退款服务返回了无效响应，请稍后重试。",
  );
}
