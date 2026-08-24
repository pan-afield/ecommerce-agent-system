import {
  isRefundApplication,
  isRefundAssessment,
  isRefundError,
} from "@/lib/refund-contract";
import type {
  RefundApplication,
  RefundAssessment,
  RefundErrorCode,
  RefundReviewDecision,
} from "@/types/refund";

export class RefundApiError extends Error {
  readonly code: RefundErrorCode;
  readonly status: number;

  constructor(code: RefundErrorCode, message: string, status: number) {
    super(message);
    this.name = "RefundApiError";
    this.code = code;
    this.status = status;
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
    response = await fetch(url, {
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
