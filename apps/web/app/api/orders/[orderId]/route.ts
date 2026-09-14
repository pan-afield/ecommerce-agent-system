import { NextResponse } from "next/server";

import { getBackendDetail } from "@/lib/backend-error";
import { getBackendOrderDetail, isOrderDetail } from "@/lib/order-contract";
import { getSessionAgentCoreAuthorization as createAgentCoreAuthorization } from "@/lib/server-auth";
import {
  ORDER_ID_MAX_LENGTH,
  type OrderError,
  type ProxyOrderErrorCode,
} from "@/types/order";

const DEFAULT_AGENT_CORE_URL = "http://localhost:8000";
const ORDER_UPSTREAM_TIMEOUT_MS = 8_000;
const UNSAFE_ORDER_ID_CHARACTERS = "/\\?#";

interface OrderRouteContext {
  params: Promise<{ orderId: string }>;
}

function errorResponse(status: number, code: ProxyOrderErrorCode, message: string) {
  return NextResponse.json<OrderError>({ error: { code, message } }, { status });
}

function isTimeoutError(error: unknown) {
  if (typeof error !== "object" || error === null || !("name" in error)) {
    return false;
  }

  return error.name === "AbortError" || error.name === "TimeoutError";
}

function hasUnsafeOrderIdCharacter(orderId: string) {
  return Array.from(orderId).some((character) => {
    const codePoint = character.codePointAt(0) ?? 0;
    return (
      codePoint <= 31 ||
      codePoint === 127 ||
      UNSAFE_ORDER_ID_CHARACTERS.includes(character)
    );
  });
}

export async function GET(_request: Request, context: OrderRouteContext) {
  const { orderId: rawOrderId } = await context.params;
  const orderId = rawOrderId.trim();

  if (
    orderId.length === 0 ||
    Array.from(orderId).length > ORDER_ID_MAX_LENGTH ||
    hasUnsafeOrderIdCharacter(orderId)
  ) {
    return errorResponse(400, "order_invalid_request", "订单 ID 格式无效。");
  }

  const agentCoreUrl = (process.env.AGENT_CORE_URL || DEFAULT_AGENT_CORE_URL).replace(/\/+$/, "");
  const authorization = await createAgentCoreAuthorization();
  if (authorization === null) {
    return errorResponse(
      503,
      "order_auth_unavailable",
      "认证服务尚未配置，请联系管理员。",
    );
  }

  let upstreamResponse: Response;
  try {
    upstreamResponse = await fetch(
      `${agentCoreUrl}/v1/orders/${encodeURIComponent(orderId)}`,
      {
        method: "GET",
        headers: { authorization },
        cache: "no-store",
        signal: AbortSignal.timeout(ORDER_UPSTREAM_TIMEOUT_MS),
      },
    );
  } catch (error) {
    if (isTimeoutError(error)) {
      return errorResponse(
        504,
        "order_upstream_timeout",
        "订单服务响应超时，请稍后重试。",
      );
    }

    return errorResponse(
      503,
      "order_upstream_unreachable",
      "无法连接订单服务，请稍后重试。",
    );
  }

  let upstreamBody: unknown;
  try {
    upstreamBody = await upstreamResponse.json();
  } catch {
    return errorResponse(
      502,
      "order_invalid_upstream_response",
      "订单服务返回了无效响应，请稍后重试。",
    );
  }

  if (upstreamResponse.ok && isOrderDetail(upstreamBody)) {
    return NextResponse.json(upstreamBody, { status: upstreamResponse.status });
  }

  const backendDetail = getBackendOrderDetail(upstreamBody);
  if (upstreamResponse.status === 401 && getBackendDetail(upstreamBody)) {
    return errorResponse(401, "order_unauthorized", "登录状态无效，请重新登录。");
  }

  if (
    upstreamResponse.status === 503 &&
    backendDetail === "认证服务尚未配置。"
  ) {
    return errorResponse(
      503,
      "order_auth_unavailable",
      "认证服务尚未配置，请联系管理员。",
    );
  }
  if (upstreamResponse.status === 404 && backendDetail) {
    return errorResponse(404, "order_not_found", backendDetail);
  }

  if (upstreamResponse.status === 503 && backendDetail) {
    return errorResponse(503, "order_service_unavailable", backendDetail);
  }

  return errorResponse(
    502,
    "order_invalid_upstream_response",
    "订单服务返回了无效响应，请稍后重试。",
  );
}
