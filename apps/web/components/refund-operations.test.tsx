import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { RefundOperationDetail } from "@/types/refund";

const { getDetailMock, getQueueMock, reviewMock, runOperationMock } = vi.hoisted(() => ({
  getDetailMock: vi.fn(),
  getQueueMock: vi.fn(),
  reviewMock: vi.fn(),
  runOperationMock: vi.fn(),
}));

vi.mock("@/lib/refund-api", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  getRefundOperation: getDetailMock,
  getRefundOperations: getQueueMock,
  reviewRefundApplication: reviewMock,
  runRefundOperation: runOperationMock,
}));

import { RefundApiError } from "@/lib/refund-api";

import { RefundOperations } from "./refund-operations";

const queueItem = {
  refund_application_id: "refund-001",
  execution_status: "RUNNING" as const,
  status: "MANUAL_REQUIRED" as const,
  attempts: 5,
  last_error_code: "NOT_FOUND",
  updated_at: "2026-09-19T08:00:00Z",
};

const detail: RefundOperationDetail = {
  execution: {
    id: "execution-001",
    status: "RUNNING",
    amount: "88.00",
    currency: "CNY",
    provider_reference: null,
  },
  recovery: {
    status: "MANUAL_REQUIRED",
    attempts: 5,
    next_attempt_at: "2026-09-19T08:01:00Z",
    last_error_code: "NOT_FOUND",
    updated_at: "2026-09-19T08:00:00Z",
  },
  events: [{
    id: "9007199254740993",
    action: "MANUAL_REQUIRED",
    source: "compensation",
    actor_user_id: null,
    source_event_id: null,
    from_status: "PROCESSING",
    to_status: "RUNNING",
    provider_reference: null,
    error_code: "NOT_FOUND",
    note: null,
    created_at: "2026-09-19T08:00:00Z",
  }],
  next_after_id: "9007199254740993",
};

describe("RefundOperations", () => {
  beforeEach(() => {
    getQueueMock.mockReset();
    getDetailMock.mockReset();
    runOperationMock.mockReset();
    reviewMock.mockReset();
    getQueueMock.mockResolvedValue({ items: [queueItem] });
    getDetailMock.mockResolvedValue(detail);
    runOperationMock.mockResolvedValue(undefined);
    reviewMock.mockResolvedValue({
      id: "refund-approval-001",
      order_id: "order-demo-001",
      request_id: "request-001",
      requested_amount: "88.00",
      currency: "CNY",
      status: "APPROVED",
    });
  });

  it("shows the manual queue and string audit identifiers", async () => {
    const user = userEvent.setup();
    render(<RefundOperations />);

    await user.click(await screen.findByRole("button", { name: /refund-001/ }));
    expect(await screen.findByText("#9007199254740993 · MANUAL_REQUIRED")).toBeInTheDocument();
    expect(screen.getByText("CNY 88.00")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "原键重投" })).toBeInTheDocument();
  });

  it("approves by application ID with a separate confirmation", async () => {
    const user = userEvent.setup();
    render(<RefundOperations />);
    await user.type(screen.getByRole("textbox", { name: "待审批退款申请编号" }), "refund-approval-001");
    await user.type(screen.getByRole("textbox", { name: "管理员审批备注" }), "已核实客户申请");
    await user.click(screen.getByRole("button", { name: "准备批准" }));
    await user.click(screen.getByRole("button", { name: "确认批准" }));

    expect(reviewMock).toHaveBeenCalledWith("refund-approval-001", "APPROVED", "已核实客户申请");
    expect(await screen.findByRole("status")).toHaveTextContent("已批准");
    expect(screen.getByText(/不代表资金已退款/)).toBeInTheDocument();
  });

  it("refreshes the selected execution after a conflicting approval", async () => {
    const user = userEvent.setup();
    getDetailMock
      .mockResolvedValueOnce({
        ...detail,
        execution: { ...detail.execution, status: "PROCESSING" },
      })
      .mockResolvedValueOnce({
        ...detail,
        execution: { ...detail.execution, status: "SUCCEEDED", provider_reference: "sandbox-001" },
      });
    reviewMock.mockRejectedValueOnce(
      new RefundApiError("refund_conflict", "退款申请已被其他审批人处理，请刷新后重试。", 409),
    );
    render(<RefundOperations />);

    await user.click(await screen.findByRole("button", { name: /refund-001/ }));
    expect(await screen.findByText("PROCESSING")).toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: "待审批退款申请编号" }), "refund-001");
    await user.click(screen.getByRole("button", { name: "准备拒绝" }));
    await user.click(screen.getByRole("button", { name: "确认拒绝" }));

    expect(await screen.findByText("SUCCEEDED")).toBeInTheDocument();
    expect(getDetailMock).toHaveBeenCalledTimes(2);
    const approval = within(screen.getByRole("region", { name: "审批申请" }));
    expect(approval.getByRole("alert")).toHaveTextContent("退款申请已被其他审批人处理，请刷新后重试。");
    expect(approval.queryByRole("button", { name: "确认拒绝" })).not.toBeInTheDocument();
    expect(reviewMock).toHaveBeenCalledTimes(1);
  });

  it("shows other approval failures beside the action and clears stale feedback on a new attempt", async () => {
    const user = userEvent.setup();
    reviewMock.mockRejectedValueOnce(
      new RefundApiError("refund_service_unavailable", "审批服务暂不可用，请稍后重试。", 503),
    );
    render(<RefundOperations />);

    const approval = within(screen.getByRole("region", { name: "审批申请" }));
    await user.type(approval.getByRole("textbox", { name: "待审批退款申请编号" }), "refund-approval-001");
    await user.click(approval.getByRole("button", { name: "准备拒绝" }));
    await user.click(approval.getByRole("button", { name: "确认拒绝" }));

    expect(await approval.findByRole("alert")).toHaveTextContent("审批服务暂不可用，请稍后重试。");
    await user.click(approval.getByRole("button", { name: "取消" }));
    await user.click(approval.getByRole("button", { name: "准备批准" }));
    expect(approval.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("requires a note and explicit confirmation before an original-key resubmit", async () => {
    const user = userEvent.setup();
    render(<RefundOperations />);
    await user.click(await screen.findByRole("button", { name: /refund-001/ }));
    await user.click(await screen.findByRole("button", { name: "原键重投" }));

    const submit = screen.getByRole("button", { name: "确认原键重投" });
    expect(submit).toBeDisabled();
    await user.type(screen.getByRole("textbox", { name: "退款运维备注" }), "已核实沙箱无原请求");
    expect(submit).toBeDisabled();
    await user.click(screen.getByRole("checkbox"));
    expect(submit).toBeEnabled();
    await user.click(submit);

    expect(runOperationMock).toHaveBeenCalledWith("refund-001", "resubmit", "已核实沙箱无原请求");
    await waitFor(() => expect(getDetailMock).toHaveBeenCalledTimes(2));
    expect(getQueueMock).toHaveBeenCalledTimes(2);
  });

  it("does not describe conflict acknowledgement as changing the final status", async () => {
    const user = userEvent.setup();
    getQueueMock.mockResolvedValueOnce({ items: [queueItem] }).mockResolvedValueOnce({ items: [] });
    getDetailMock.mockResolvedValueOnce({
      ...detail,
      execution: { ...detail.execution, status: "SUCCEEDED" },
      recovery: { ...detail.recovery!, last_error_code: "TERMINAL_CONFLICT" },
    }).mockResolvedValueOnce({
      ...detail,
      execution: { ...detail.execution, status: "SUCCEEDED" },
      recovery: { ...detail.recovery!, status: "COMPLETED", last_error_code: "TERMINAL_CONFLICT" },
      events: [
        ...detail.events,
        { ...detail.events[0]!, id: "9007199254740994", action: "CONFLICT_ACKNOWLEDGED" },
      ],
    });
    render(<RefundOperations />);
    await user.click(await screen.findByRole("button", { name: /refund-001/ }));
    await user.click(await screen.findByRole("button", { name: "确认已核查矛盾" }));

    expect(screen.getByText("此操作只记录已人工核查，不会修改退款终态。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "原键重投" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "恢复有限补查" })).not.toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: "退款运维备注" }), "已向渠道核实终态");
    await user.click(screen.getByRole("button", { name: "确认确认已核查矛盾" }));

    expect(runOperationMock).toHaveBeenCalledWith("refund-001", "acknowledge-conflict", "已向渠道核实终态");
    expect(await screen.findByText("#9007199254740994 · CONFLICT_ACKNOWLEDGED")).toBeInTheDocument();
    expect(screen.getByText("SUCCEEDED")).toBeInTheDocument();
    expect(screen.getByText("COMPLETED")).toBeInTheDocument();
    expect(screen.getByText("当前没有待人工处理项")).toBeInTheDocument();
  });

  it("loads the next audit page with the exact string cursor", async () => {
    const user = userEvent.setup();
    const events = Array.from({ length: 50 }, (_, index) => ({
      ...detail.events[0]!,
      id: String(9007199254740993n + BigInt(index)),
    }));
    getDetailMock
      .mockResolvedValueOnce({ ...detail, events, next_after_id: events[49]!.id })
      .mockResolvedValueOnce({ ...detail, events: [{ ...detail.events[0]!, id: "9007199254741099" }], next_after_id: "9007199254741099" });
    render(<RefundOperations />);
    await user.click(await screen.findByRole("button", { name: /refund-001/ }));
    await user.click(await screen.findByRole("button", { name: "加载更多审计" }));

    expect(getDetailMock).toHaveBeenLastCalledWith("refund-001", events[49]!.id, 50);
    expect(await screen.findByText("#9007199254741099 · MANUAL_REQUIRED")).toBeInTheDocument();
    expect(screen.getByText(`#${events[0]!.id} · MANUAL_REQUIRED`)).toBeInTheDocument();
    expect(screen.getAllByText(/#900719925474\d+ · MANUAL_REQUIRED/)).toHaveLength(51);
    expect(screen.queryByRole("button", { name: "加载更多审计" })).not.toBeInTheDocument();
  });

  it("stops paging after an empty page when the first page has exactly fifty events", async () => {
    const user = userEvent.setup();
    const events = Array.from({ length: 50 }, (_, index) => ({
      ...detail.events[0]!,
      id: String(9007199254740993n + BigInt(index)),
    }));
    getDetailMock
      .mockResolvedValueOnce({ ...detail, events, next_after_id: events[49]!.id })
      .mockResolvedValueOnce({ ...detail, events: [], next_after_id: events[49]!.id });
    render(<RefundOperations />);

    await user.click(await screen.findByRole("button", { name: /refund-001/ }));
    await user.click(screen.getByRole("button", { name: "加载更多审计" }));

    await waitFor(() => expect(screen.queryByRole("button", { name: "加载更多审计" })).not.toBeInTheDocument());
    expect(screen.getAllByText(/#900719925474\d+ · MANUAL_REQUIRED/)).toHaveLength(50);
    expect(getDetailMock).toHaveBeenCalledTimes(2);
  });

  it("keeps authorization and service failures visible", async () => {
    getQueueMock.mockRejectedValue(
      new RefundApiError("refund_forbidden", "当前身份无权执行此退款操作。", 403),
    );
    render(<RefundOperations />);
    expect(await screen.findByRole("alert")).toHaveTextContent("当前身份无权执行此退款操作。");
    expect(screen.queryByText("当前没有待人工处理项")).not.toBeInTheDocument();
  });

  it("exposes reduced-motion mode through the operation detail transition", async () => {
    const user = userEvent.setup();
    render(<RefundOperations />);
    await user.click(await screen.findByRole("button", { name: /refund-001/ }));
    expect(await screen.findByText("审计记录")).toBeInTheDocument();
  });
});
