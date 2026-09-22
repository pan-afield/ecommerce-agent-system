import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { RefundApplication, RefundExecution } from "@/types/refund";

const { executeMock, getExecutionMock, recoverMock } = vi.hoisted(() => ({
  executeMock: vi.fn(),
  getExecutionMock: vi.fn(),
  recoverMock: vi.fn(),
}));

vi.mock("@/lib/refund-api", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  executeRefund: executeMock,
  getRefundExecution: getExecutionMock,
  recoverRefundExecution: recoverMock,
}));

import { RefundApiError } from "@/lib/refund-api";

import { RefundExecutionPanel } from "./refund-execution";

const application: RefundApplication = {
  id: "refund-001",
  order_id: "order-demo-001",
  request_id: "request-001",
  requested_amount: "88.00",
  currency: "CNY",
  status: "APPROVED",
};

const processing: RefundExecution = {
  id: "execution-001",
  status: "PROCESSING",
  amount: "88.00",
  currency: "CNY",
  provider_reference: null,
};

describe("RefundExecutionPanel", () => {
  beforeEach(() => {
    executeMock.mockReset();
    getExecutionMock.mockReset();
    recoverMock.mockReset();
  });

  it("requires explicit confirmation and prevents duplicate execution", async () => {
    const user = userEvent.setup();
    getExecutionMock.mockResolvedValue(null);
    let resolveExecution: ((value: RefundExecution) => void) | undefined;
    executeMock.mockReturnValue(new Promise((resolve) => { resolveExecution = resolve; }));
    render(<RefundExecutionPanel application={application} reduceMotion={false} />);

    await user.click(await screen.findByRole("button", { name: "开始执行退款" }));
    const confirm = screen.getByRole("button", { name: "确认执行退款" });
    await user.click(confirm);
    expect(confirm).toBeDisabled();
    await user.click(confirm);
    expect(executeMock).toHaveBeenCalledTimes(1);

    await act(async () => resolveExecution?.(processing));
    expect((await screen.findAllByText("渠道处理中")).length).toBeGreaterThan(0);
    expect(screen.getByText(/审批通过本身不代表退款完成/)).toBeInTheDocument();
  });

  it("treats execution timeout as unknown and only offers a status lookup", async () => {
    const user = userEvent.setup();
    getExecutionMock.mockResolvedValueOnce(null);
    executeMock.mockRejectedValue(new RefundApiError("refund_outcome_unknown", "退款结果暂时未知。", 503));
    getExecutionMock.mockResolvedValueOnce(processing);
    render(<RefundExecutionPanel application={application} reduceMotion={false} />);

    await user.click(await screen.findByRole("button", { name: "开始执行退款" }));
    await user.click(screen.getByRole("button", { name: "确认执行退款" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("不要重复提交退款");
    expect(screen.queryByRole("button", { name: "确认执行退款" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查询执行状态" }));
    expect((await screen.findAllByText("渠道处理中")).length).toBeGreaterThan(0);
    expect(executeMock).toHaveBeenCalledTimes(1);
  });

  it("keeps a missing or unauthorized application as an error", async () => {
    getExecutionMock.mockRejectedValue(
      new RefundApiError("refund_not_found", "无记录", 404),
    );
    render(<RefundExecutionPanel application={application} reduceMotion={false} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("无记录");
    expect(screen.queryByRole("button", { name: "开始执行退款" })).not.toBeInTheDocument();
  });

  it("restores success after refresh and shows the provider reference", async () => {
    getExecutionMock.mockResolvedValue({
      ...processing,
      status: "SUCCEEDED",
      provider_reference: "sandbox-reference-001",
    });
    render(<RefundExecutionPanel application={application} reduceMotion={false} />);

    expect((await screen.findAllByText("退款执行成功")).length).toBeGreaterThan(0);
    expect(screen.getByText("sandbox-reference-001")).toBeInTheDocument();
    expect(executeMock).not.toHaveBeenCalled();
  });

  it("shows only an authoritative FAILED response as failure", async () => {
    getExecutionMock.mockResolvedValue({ ...processing, status: "FAILED" });
    render(<RefundExecutionPanel application={application} reduceMotion={false} />);

    expect((await screen.findAllByText("退款执行失败")).length).toBeGreaterThan(0);
    expect(screen.getByText(/服务端已收到权威失败结果/)).toBeInTheDocument();
  });

  it("recovers a processing result without submitting a new refund", async () => {
    const user = userEvent.setup();
    getExecutionMock.mockResolvedValue(processing);
    recoverMock.mockResolvedValue({ ...processing, status: "SUCCEEDED", provider_reference: "ref-1" });
    render(<RefundExecutionPanel application={application} reduceMotion />);

    const section = (await screen.findByRole("heading", { name: "资金执行状态" })).closest("section");
    expect(section).toHaveAttribute("data-motion-mode", "reduced");
    await user.click(screen.getByRole("button", { name: "补查沙箱结果" }));
    expect((await screen.findAllByText("退款执行成功")).length).toBeGreaterThan(0);
    expect(recoverMock).toHaveBeenCalledWith("refund-001");
    expect(executeMock).not.toHaveBeenCalled();
  });
});
