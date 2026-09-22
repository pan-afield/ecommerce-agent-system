import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { OrderDetail } from "@/types/order";
import type { RefundApplication } from "@/types/refund";

const {
  assessRefundMock,
  confirmRefundMock,
  createRefundMock,
  executeRefundMock,
  getCurrentRefundMock,
  getExecutionMock,
  reviewRefundMock,
} = vi.hoisted(() => ({
  assessRefundMock: vi.fn(),
  confirmRefundMock: vi.fn(),
  createRefundMock: vi.fn(),
  executeRefundMock: vi.fn(),
  getCurrentRefundMock: vi.fn(),
  getExecutionMock: vi.fn(),
  reviewRefundMock: vi.fn(),
}));

vi.mock("@/lib/refund-api", async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    assessRefund: assessRefundMock,
    confirmRefundApplication: confirmRefundMock,
    createRefundApplication: createRefundMock,
    executeRefund: executeRefundMock,
    getCurrentRefundApplication: getCurrentRefundMock,
    getRefundExecution: getExecutionMock,
    reviewRefundApplication: reviewRefundMock,
  };
});

import { RefundApiError } from "@/lib/refund-api";
import { REFUND_SESSION_STORAGE_KEY } from "@/lib/refund-session";

import { RefundFlow } from "./refund-flow";

const order: OrderDetail = {
  id: "order-demo-001",
  order_number: "EC-20260810-001",
  status: "shipped",
  total_amount: "299.00",
  currency: "CNY",
  created_at: "2026-08-10T08:30:00Z",
  shipment_events: [],
};

const awaitingApplication: RefundApplication = {
  id: "refund-001",
  order_id: "order-demo-001",
  request_id: "refund-request-001",
  requested_amount: "88.00",
  currency: "CNY",
  status: "AWAITING_CUSTOMER_CONFIRMATION",
  created: true,
};

function seedApplication(application: RefundApplication) {
  window.sessionStorage.setItem(
    REFUND_SESSION_STORAGE_KEY,
    JSON.stringify(application),
  );
}

describe("RefundFlow", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    assessRefundMock.mockReset();
    confirmRefundMock.mockReset();
    createRefundMock.mockReset();
    executeRefundMock.mockReset();
    getCurrentRefundMock.mockReset();
    getExecutionMock.mockReset();
    reviewRefundMock.mockReset();
    getExecutionMock.mockRejectedValue(
      new RefundApiError("refund_not_found", "无记录", 404),
    );
    getCurrentRefundMock.mockImplementation(async () => {
      const rawApplication = window.sessionStorage.getItem(REFUND_SESSION_STORAGE_KEY);
      return rawApplication ? (JSON.parse(rawApplication) as RefundApplication) : null;
    });
  });

  it("does not render without an order or a paused application", () => {
    render(<RefundFlow approvalDemoEnabled order={null} reduceMotion={false} />);
    expect(screen.queryByRole("heading", { name: "退款申请" })).not.toBeInTheDocument();
  });

  it("shows the application entry only after the authoritative GET reports no application", async () => {
    let resolveLookup: ((value: null) => void) | null = null;
    getCurrentRefundMock.mockReturnValue(
      new Promise((resolve) => {
        resolveLookup = resolve;
      }),
    );
    render(<RefundFlow order={order} reduceMotion={false} />);

    expect(await screen.findByRole("status")).toHaveTextContent("正在读取当前退款申请");
    await act(async () => resolveLookup?.(null));
    expect(await screen.findByRole("button", { name: "申请退款" })).toBeEnabled();
    expect(getCurrentRefundMock).toHaveBeenCalledWith("order-demo-001");
  });

  it("replaces stale session data with the current backend application on refresh", async () => {
    seedApplication(awaitingApplication);
    getCurrentRefundMock.mockResolvedValue({
      ...awaitingApplication,
      id: "refund-authoritative",
      request_id: "refund-request-authoritative",
      requested_amount: "66.00",
      status: "APPROVED",
      created: undefined,
    });
    render(<RefundFlow order={null} reduceMotion={false} />);

    expect(await screen.findByRole("heading", { name: "审批已批准" })).toBeInTheDocument();
    expect(screen.getByText("CNY 66.00")).toBeInTheDocument();
    expect(screen.getByText("refund-authoritative")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "申请退款" })).not.toBeInTheDocument();
    expect(getCurrentRefundMock).toHaveBeenCalledWith("order-demo-001");
  });

  it("refreshes a pending application into the approved state with one current-application GET", async () => {
    const user = userEvent.setup();
    const pendingApplication = { ...awaitingApplication, status: "PENDING_MANUAL_APPROVAL" as const };
    const approvedApplication = {
      ...pendingApplication,
      status: "APPROVED" as const,
      reviewed_by_user_id: "staff-zhang",
      reviewed_at: "2026-08-23T10:00:00Z",
      review_note: "已人工核对。",
    };
    let resolveRefresh: ((application: RefundApplication) => void) | null = null;
    getCurrentRefundMock
      .mockResolvedValueOnce(pendingApplication)
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolveRefresh = resolve;
        }),
      );
    seedApplication(pendingApplication);
    render(<RefundFlow order={order} reduceMotion={false} sessionScope="chat-message:one" />);

    expect(await screen.findByText("申请已暂停，等待人工决定")).toBeInTheDocument();
    const refreshButton = screen.getByRole("button", { name: "刷新申请状态" });
    await user.click(refreshButton);

    expect(refreshButton).toBeDisabled();
    expect(await screen.findByRole("status")).toHaveTextContent("正在同步退款状态");
    await act(async () => {
      resolveRefresh?.(approvedApplication);
    });
    expect(await screen.findByRole("heading", { name: "审批已批准" })).toBeInTheDocument();
    expect(screen.getByText("审批人：staff-zhang")).toBeInTheDocument();
    expect(screen.getByText("审批备注：已人工核对。")).toBeInTheDocument();
    expect(screen.getByText(/审批时间：/)).toBeInTheDocument();
    expect(getCurrentRefundMock).toHaveBeenCalledTimes(2);
    expect(getCurrentRefundMock).toHaveBeenNthCalledWith(2, "order-demo-001");
    expect(createRefundMock).not.toHaveBeenCalled();
    expect(confirmRefundMock).not.toHaveBeenCalled();
    expect(reviewRefundMock).not.toHaveBeenCalled();
    expect(executeRefundMock).not.toHaveBeenCalled();
  });

  it("keeps the current application and exposes retry when refreshing fails", async () => {
    const user = userEvent.setup();
    const pendingApplication = { ...awaitingApplication, status: "PENDING_MANUAL_APPROVAL" as const };
    getCurrentRefundMock
      .mockResolvedValueOnce(pendingApplication)
      .mockRejectedValueOnce(
        new RefundApiError("refund_service_unavailable", "退款服务暂时不可用，请稍后重试。", 503),
      );
    seedApplication(pendingApplication);
    render(<RefundFlow order={order} reduceMotion={false} sessionScope="chat-message:one" />);

    expect(await screen.findByText("申请已暂停，等待人工决定")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "刷新申请状态" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("退款服务暂时不可用，请稍后重试。");
    expect(screen.getByText("申请已暂停，等待人工决定")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试退款操作" })).toBeEnabled();
  });

  it("updates only the selected refund flow when multiple chat orders refresh", async () => {
    const user = userEvent.setup();
    const firstOrder = order;
    const secondOrder = { ...order, id: "order-demo-002", order_number: "EC-20260810-002" };
    const firstPending = {
      ...awaitingApplication,
      order_id: firstOrder.id,
      status: "PENDING_MANUAL_APPROVAL" as const,
    };
    const secondPending = {
      ...awaitingApplication,
      id: "refund-002",
      order_id: secondOrder.id,
      status: "PENDING_MANUAL_APPROVAL" as const,
    };
    const firstApproved = {
      ...firstPending,
      status: "APPROVED" as const,
      reviewed_by_user_id: "staff-zhang",
      reviewed_at: "2026-08-23T10:00:00Z",
      review_note: "已人工核对。",
    };
    const lookupCounts = new Map<string, number>();
    getCurrentRefundMock.mockImplementation(async (orderId: string) => {
      const count = lookupCounts.get(orderId) ?? 0;
      lookupCounts.set(orderId, count + 1);
      if (orderId === firstOrder.id) {
        return count === 0 ? firstPending : firstApproved;
      }
      return secondPending;
    });
    render(
      <>
        <RefundFlow order={firstOrder} reduceMotion={false} sessionScope="chat-message:first" />
        <RefundFlow order={secondOrder} reduceMotion={false} sessionScope="chat-message:second" />
      </>,
    );

    expect(await screen.findAllByText("申请已暂停，等待人工决定")).toHaveLength(2);
    await user.click(screen.getAllByRole("button", { name: "刷新申请状态" })[0]!);

    await waitFor(() => {
      expect(screen.getByText("审批人：staff-zhang")).toBeInTheDocument();
      expect(screen.getAllByText("申请已暂停，等待人工决定")).toHaveLength(1);
    });
    expect(lookupCounts.get(firstOrder.id)).toBe(2);
    expect(lookupCounts.get(secondOrder.id)).toBe(1);
  });

  it("keeps a GET 503 as an error instead of exposing the application entry", async () => {
    const user = userEvent.setup();
    getCurrentRefundMock
      .mockRejectedValueOnce(
        new RefundApiError(
          "refund_service_unavailable",
          "退款服务暂时不可用，请稍后重试。",
          503,
        ),
      )
      .mockResolvedValueOnce(null);
    render(<RefundFlow order={order} reduceMotion={false} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "退款服务暂时不可用，请稍后重试。",
    );
    expect(screen.queryByRole("button", { name: "申请退款" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "重试读取退款申请" }));
    expect(await screen.findByRole("button", { name: "申请退款" })).toBeEnabled();
    expect(getCurrentRefundMock).toHaveBeenCalledTimes(2);
  });

  it("assesses a low amount, creates one idempotent application, and shows the confirmation summary", async () => {
    const user = userEvent.setup();
    assessRefundMock.mockResolvedValue({
      eligible_for_review: true,
      reason: "eligible_for_review",
      requires_customer_confirmation: true,
    });
    createRefundMock.mockResolvedValue(awaitingApplication);
    render(<RefundFlow order={order} reduceMotion={false} />);

    await user.click(await screen.findByRole("button", { name: "申请退款" }));
    await user.click(screen.getByRole("button", { name: "检查资格" }));

    expect(assessRefundMock).toHaveBeenCalledWith("order-demo-001", "88.00", "CNY");
    expect(await screen.findByText("归属、金额、币种和订单状态校验通过")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "创建申请" }));

    expect(createRefundMock).toHaveBeenCalledWith(
      "order-demo-001",
      expect.stringMatching(/^refund-request-/),
      "88.00",
      "CNY",
    );
    expect(await screen.findByText("请确认将申请提交人工审批")).toBeInTheDocument();
    expect(screen.getByText("CNY 88.00")).toBeInTheDocument();
    expect(screen.getByText(/不会直接执行支付退款/)).toBeInTheDocument();
  });

  it("renders the backend rejection for an amount above the order total", async () => {
    const user = userEvent.setup();
    assessRefundMock.mockResolvedValue({
      eligible_for_review: false,
      reason: "amount_exceeds_order_total",
      requires_customer_confirmation: false,
    });
    render(<RefundFlow order={order} reduceMotion={false} />);

    await user.click(await screen.findByRole("button", { name: "申请退款" }));
    const amount = screen.getByRole("textbox", { name: "退款金额" });
    await user.clear(amount);
    await user.type(amount, "299.01");
    await user.click(screen.getByRole("button", { name: "检查资格" }));

    expect(await screen.findByText("退款金额超过订单金额")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "创建申请" })).not.toBeInTheDocument();
  });

  it("adopts every authoritative field when create returns an existing application", async () => {
    const user = userEvent.setup();
    assessRefundMock.mockResolvedValue({
      eligible_for_review: true,
      reason: "eligible_for_review",
      requires_customer_confirmation: true,
    });
    createRefundMock.mockResolvedValue({
      ...awaitingApplication,
      id: "refund-existing-001",
      request_id: "refund-request-existing",
      requested_amount: "66.00",
      status: "PENDING_MANUAL_APPROVAL",
      created: false,
    });
    render(<RefundFlow order={order} reduceMotion={false} />);

    await user.click(await screen.findByRole("button", { name: "申请退款" }));
    await user.click(screen.getByRole("button", { name: "检查资格" }));
    await user.click(await screen.findByRole("button", { name: "创建申请" }));

    expect(await screen.findByText("申请已暂停，等待人工决定")).toBeInTheDocument();
    expect(screen.getByText("CNY 66.00")).toBeInTheDocument();
    expect(screen.getByText("refund-existing-001")).toBeInTheDocument();
    expect(screen.queryByText("CNY 88.00")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "申请退款" })).not.toBeInTheDocument();
    expect(
      JSON.parse(window.sessionStorage.getItem(REFUND_SESSION_STORAGE_KEY) ?? "{}"),
    ).toMatchObject({
      id: "refund-existing-001",
      request_id: "refund-request-existing",
      requested_amount: "66.00",
      status: "PENDING_MANUAL_APPROVAL",
    });
  });

  it("offers a new application after the backend no longer returns a rejected record", async () => {
    seedApplication({ ...awaitingApplication, status: "REJECTED" });
    getCurrentRefundMock.mockResolvedValue(null);
    render(<RefundFlow order={null} reduceMotion={false} />);

    expect(await screen.findByRole("button", { name: "申请退款" })).toBeEnabled();
    expect(screen.queryByText("审批已拒绝")).not.toBeInTheDocument();
  });

  it("does not turn a customer deferral into a backend rejection", async () => {
    const user = userEvent.setup();
    seedApplication(awaitingApplication);
    render(<RefundFlow approvalDemoEnabled order={null} reduceMotion={false} />);

    expect(await screen.findByText("已恢复本标签页最近一次服务端状态")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认退款信息" }));
    await user.click(screen.getByRole("button", { name: "暂不确认" }));

    expect(screen.getByText("等待客户确认")).toBeInTheDocument();
    expect(confirmRefundMock).not.toHaveBeenCalled();
  });

  it("requires a second customer confirmation and disables duplicate submits", async () => {
    const user = userEvent.setup();
    seedApplication(awaitingApplication);
    let resolveConfirmation: ((value: RefundApplication) => void) | null = null;
    confirmRefundMock.mockReturnValue(
      new Promise((resolve) => {
        resolveConfirmation = resolve;
      }),
    );
    render(<RefundFlow approvalDemoEnabled order={null} reduceMotion={false} />);

    await screen.findByText("等待客户确认");
    await user.click(screen.getByRole("button", { name: "确认退款信息" }));
    const confirmButton = screen.getByRole("button", { name: "再次确认提交人工审批" });
    await user.click(confirmButton);

    expect(confirmButton).toBeDisabled();
    expect(confirmRefundMock).toHaveBeenCalledTimes(1);
    await user.click(confirmButton);
    expect(confirmRefundMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveConfirmation?.({ ...awaitingApplication, status: "PENDING_MANUAL_APPROVAL" });
    });
    expect(await screen.findByText("申请已暂停，等待人工决定")).toBeInTheDocument();
  });

  it.each([
    ["APPROVED", "审批已批准", "确认批准退款申请"],
    ["REJECTED", "审批已拒绝", "确认拒绝退款申请"],
  ] as const)("renders the authoritative %s review result", async (status, label, actionName) => {
    const user = userEvent.setup();
    seedApplication({ ...awaitingApplication, status: "PENDING_MANUAL_APPROVAL" });
    reviewRefundMock.mockResolvedValue({
      ...awaitingApplication,
      status,
      reviewed_by_user_id: "staff-zhang",
      reviewed_at: "2026-08-23T10:00:00Z",
      review_note: "已人工核对。",
    });
    render(<RefundFlow approvalDemoEnabled order={null} reduceMotion={false} />);

    await screen.findByText("申请已暂停，等待人工决定");
    await user.click(
      screen.getByRole("button", { name: status === "APPROVED" ? "准备批准" : "准备拒绝" }),
    );
    await user.type(screen.getByRole("textbox", { name: "审批备注" }), "已人工核对。");
    await user.click(screen.getByRole("button", { name: actionName }));

    expect(reviewRefundMock).toHaveBeenCalledWith(
      "refund-001",
      status,
      "已人工核对。",
    );
    expect(await screen.findByRole("heading", { name: label })).toBeInTheDocument();
    if (status === "APPROVED") {
      expect(screen.getByText(/尚不能视为已退款/)).toBeInTheDocument();
    } else {
      expect(screen.getByText(/未进入资金执行/)).toBeInTheDocument();
    }
  });

  it("keeps a full-order refund in the same manual approval path", async () => {
    const user = userEvent.setup();
    assessRefundMock.mockResolvedValue({
      eligible_for_review: true,
      reason: "eligible_for_review",
      requires_customer_confirmation: true,
    });
    render(<RefundFlow order={order} reduceMotion={false} />);

    await user.click(await screen.findByRole("button", { name: "申请退款" }));
    const amount = screen.getByRole("textbox", { name: "退款金额" });
    await user.clear(amount);
    await user.type(amount, "299.00");
    await user.click(screen.getByRole("button", { name: "检查资格" }));

    expect(await screen.findByRole("button", { name: "创建申请" })).toBeEnabled();
    expect(screen.getByText(/随后交由人工审批/)).toBeInTheDocument();
    expect(assessRefundMock).toHaveBeenCalledWith("order-demo-001", "299.00", "CNY");
  });

  it("shows stable authorization and conflict errors with an idempotent retry", async () => {
    const user = userEvent.setup();
    seedApplication({ ...awaitingApplication, status: "PENDING_MANUAL_APPROVAL" });
    reviewRefundMock
      .mockRejectedValueOnce(
        new RefundApiError("refund_forbidden", "当前身份无权审批退款申请。", 403),
      )
      .mockRejectedValueOnce(
        new RefundApiError("refund_conflict", "退款申请已被其他审批人处理。", 409),
      );
    render(<RefundFlow approvalDemoEnabled order={null} reduceMotion={false} />);

    await screen.findByText("申请已暂停，等待人工决定");
    await user.click(screen.getByRole("button", { name: "准备批准" }));
    await user.click(screen.getByRole("button", { name: "确认批准退款申请" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("当前身份无权审批退款申请。");
    await user.click(screen.getByRole("button", { name: "重试退款操作" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("退款申请已被其他审批人处理。");
    expect(reviewRefundMock).toHaveBeenCalledTimes(2);
  });

  it("reuses the persisted idempotency key after an interrupted create and refresh", async () => {
    const user = userEvent.setup();
    assessRefundMock.mockResolvedValue({
      eligible_for_review: true,
      reason: "eligible_for_review",
      requires_customer_confirmation: true,
    });
    createRefundMock
      .mockRejectedValueOnce(
        new RefundApiError("refund_network_error", "网络连接失败，请检查连接后重试。", 0),
      )
      .mockResolvedValueOnce(awaitingApplication);
    const firstRender = render(<RefundFlow order={order} reduceMotion={false} />);

    await user.click(await screen.findByRole("button", { name: "申请退款" }));
    await user.click(screen.getByRole("button", { name: "检查资格" }));
    await user.click(await screen.findByRole("button", { name: "创建申请" }));
    await screen.findByRole("alert");
    const firstRequestId = createRefundMock.mock.calls[0]?.[1];

    firstRender.unmount();
    render(<RefundFlow order={order} reduceMotion={false} />);
    await user.click(await screen.findByRole("button", { name: "检查资格" }));
    await user.click(await screen.findByRole("button", { name: "创建申请" }));

    expect(createRefundMock.mock.calls[1]?.[1]).toBe(firstRequestId);
    expect(await screen.findByText("请确认将申请提交人工审批")).toBeInTheDocument();
  });

  it("restores the paused state with reduced motion disabled", async () => {
    seedApplication({ ...awaitingApplication, status: "PENDING_MANUAL_APPROVAL" });
    render(<RefundFlow order={null} reduceMotion />);

    const title = await screen.findByRole("heading", { name: "退款申请" });
    const section = title.closest("section");
    expect(section).toHaveAttribute("data-motion-mode", "reduced");
    await waitFor(() => expect(section).toHaveStyle({ opacity: "1" }));
    expect(screen.getByText("等待人工审批")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "准备批准" })).not.toBeInTheDocument();
    expect(screen.getByText(/受信任的审批后台/)).toBeInTheDocument();
  });
});
