import {
  isRefundApplication,
  isRefundAssessment,
  isRefundError,
  isRefundExecution,
  isRefundOperationDetail,
  isRefundOperationQueue,
} from "@/lib/refund-contract";
import { authenticatedFetch } from "@/lib/authenticated-fetch";
import { parseRetryAfterSeconds } from "@/lib/retry-after";
import type {
  RefundApplication,
  RefundAssessment,
  RefundErrorCode,
  RefundExecution,
  RefundOperationAction,
  RefundOperationDetail,
  RefundOperationQueue,
  RefundReviewDecision,
} from "@/types/refund";

export class RefundApiError extends Error {
  readonly code: RefundErrorCode;
  readonly status: number;
  readonly retryAfterSeconds?: number;

  constructor(code: RefundErrorCode, message: string, status: number, retryAfterSeconds?: number) {
    super(message);
    this.name = "RefundApiError";
    this.code = code;
    this.status = status;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

async function postRefund<T>(
  url: string,
  body: unknown,
  isSuccess: (value: unknown) => value is T,
  method: "GET" | "POST" = "POST",
): Promise<T> {
  let response: Response;
  try {
    response = await authenticatedFetch(url, {
      method,
      headers: { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new RefundApiError(
      "refund_network_error",
      "网络连接失败，请检查连接后重试。",
      0,
    );
  }

  let responseBody: unknown;
  try {
    responseBody = await response.json();
  } catch {
    throw new RefundApiError(
      "refund_invalid_response",
      "退款服务返回了无法识别的响应，请稍后重试。",
      response.status,
    );
  }

  if (!response.ok) {
    if (isRefundError(responseBody)) {
      throw new RefundApiError(
        responseBody.error.code,
        responseBody.error.message,
        response.status,
        parseRetryAfterSeconds(response.headers.get("retry-after")),
      );
    }
    throw new RefundApiError(
      "refund_invalid_response",
      "退款服务返回了无法识别的错误，请稍后重试。",
      response.status,
    );
  }

  if (!isSuccess(responseBody)) {
    throw new RefundApiError(
      "refund_invalid_response",
      "退款服务返回了无法识别的响应，请稍后重试。",
      response.status,
    );
  }

  return responseBody;
}

export async function getCurrentRefundApplication(
  orderId: string,
): Promise<RefundApplication | null> {
  const normalizedOrderId = orderId.trim();
  try {
    return await postRefund(
      `/api/orders/${encodeURIComponent(normalizedOrderId)}/refund-application`,
      undefined,
      isRefundApplication,
      "GET",
    );
  } catch (error) {
    if (
      error instanceof RefundApiError &&
      error.status === 404 &&
      error.message === "退款申请不存在。"
    ) {
      return null;
    }
    throw error;
  }
}

export function assessRefund(
  orderId: string,
  requestedAmount: string,
  requestedCurrency: string,
) {
  return postRefund(
    `/api/orders/${encodeURIComponent(orderId)}/refund-assessment`,
    {
      requested_amount: requestedAmount,
      requested_currency: requestedCurrency,
    },
    isRefundAssessment,
  ) satisfies Promise<RefundAssessment>;
}

export function createRefundApplication(
  orderId: string,
  requestId: string,
  requestedAmount: string,
  requestedCurrency: string,
) {
  return postRefund(
    `/api/orders/${encodeURIComponent(orderId)}/refund-applications`,
    {
      request_id: requestId,
      requested_amount: requestedAmount,
      requested_currency: requestedCurrency,
    },
    isRefundApplication,
  ) satisfies Promise<RefundApplication>;
}

export function confirmRefundApplication(applicationId: string) {
  return postRefund(
    `/api/refund-applications/${encodeURIComponent(applicationId)}/confirm`,
    undefined,
    isRefundApplication,
  ) satisfies Promise<RefundApplication>;
}

export function reviewRefundApplication(
  applicationId: string,
  decision: RefundReviewDecision,
  reviewNote: string,
) {
  return postRefund(
    `/api/refund-applications/${encodeURIComponent(applicationId)}/review`,
    { decision, review_note: reviewNote },
    isRefundApplication,
  ) satisfies Promise<RefundApplication>;
}

export function getRefundExecution(applicationId: string) {
  return postRefund(
    `/api/refund-applications/${encodeURIComponent(applicationId)}/execution`,
    undefined,
    isRefundExecution,
    "GET",
  ) satisfies Promise<RefundExecution>;
}

export function executeRefund(applicationId: string) {
  return postRefund(
    `/api/refund-applications/${encodeURIComponent(applicationId)}/execute`,
    undefined,
    isRefundExecution,
  ) satisfies Promise<RefundExecution>;
}

export function recoverRefundExecution(applicationId: string) {
  return postRefund(
    `/api/refund-applications/${encodeURIComponent(applicationId)}/recover`,
    undefined,
    isRefundExecution,
  ) satisfies Promise<RefundExecution>;
}

export function getRefundOperations(limit = 50) {
  return postRefund(
    `/api/refund-operations?limit=${limit}`,
    undefined,
    isRefundOperationQueue,
    "GET",
  ) satisfies Promise<RefundOperationQueue>;
}

export function getRefundOperation(
  applicationId: string,
  afterId = "0",
  limit = 50,
) {
  return postRefund(
    `/api/refund-operations/${encodeURIComponent(applicationId)}?after_id=${encodeURIComponent(afterId)}&limit=${limit}`,
    undefined,
    isRefundOperationDetail,
    "GET",
  ) satisfies Promise<RefundOperationDetail>;
}

export async function runRefundOperation(
  applicationId: string,
  action: RefundOperationAction,
  note: string,
) {
  let response: Response;
  try {
    response = await authenticatedFetch(
      `/api/refund-operations/${encodeURIComponent(applicationId)}/${action}`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ note }),
      },
    );
  } catch {
    throw new RefundApiError("refund_network_error", "网络连接失败，请检查连接后重试。", 0);
  }

  if (response.status === 204) return;

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new RefundApiError(
      "refund_invalid_response",
      "退款运维服务返回了无法识别的响应，请稍后重试。",
      response.status,
    );
  }
  if (isRefundError(body)) {
    throw new RefundApiError(
      body.error.code,
      body.error.message,
      response.status,
      parseRetryAfterSeconds(response.headers.get("retry-after")),
    );
  }
  throw new RefundApiError(
    "refund_invalid_response",
    "退款运维服务返回了无法识别的错误，请稍后重试。",
    response.status,
  );
}
