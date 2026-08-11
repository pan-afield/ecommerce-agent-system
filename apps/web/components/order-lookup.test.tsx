import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { OrderDetail } from "@/types/order";

const { getOrderMock, useReducedMotionMock } = vi.hoisted(() => ({
  getOrderMock: vi.fn(),
  useReducedMotionMock: vi.fn(),
}));

vi.mock("@/lib/order-api", async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return { ...actual, getOrder: getOrderMock };
});

vi.mock("motion/react", async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return { ...actual, useReducedMotion: useReducedMotionMock };
});

import { OrderApiError } from "@/lib/order-api";

import { OrderLookup } from "./order-lookup";

const orderFixture: OrderDetail = {
  id: "order-demo-001",
  order_number: "EC-20260810-001",
  status: "shipped",
  total_amount: "299.00",
  currency: "CNY",
  created_at: "2026-08-10T08:30:00Z",
  shipment_events: [
    {
      id: "shipment-event-002",
      status: "in_transit",
      description: "包裹运输中",
      location: "上海市",
      occurred_at: "2026-08-09T02:30:00Z",
    },
    {
      id: "shipment-event-001",
      status: "confirmed",
      description: "商家已确认订单",
      location: "杭州市",
      occurred_at: "2026-08-08T01:15:00Z",
    },
  ],
};

describe("OrderLookup", () => {
  beforeEach(() => {
    getOrderMock.mockReset();
    useReducedMotionMock.mockReset();
    useReducedMotionMock.mockReturnValue(false);
  });

  it("starts with the demo order and disables empty input", async () => {
    const user = userEvent.setup();
    render(<OrderLookup />);

    const input = screen.getByRole("textbox", { name: "订单 ID" });
    expect(input).toHaveValue("order-demo-001");
    expect(screen.getByRole("button", { name: "查询订单" })).toBeEnabled();

    await user.clear(input);
    await user.type(input, "   ");
    expect(screen.getByRole("button", { name: "查询订单" })).toBeDisabled();
    expect(getOrderMock).not.toHaveBeenCalled();
  });

  it("disables duplicate queries while loading", async () => {
    const user = userEvent.setup();
    let resolveRequest: ((value: OrderDetail) => void) | null = null;
    getOrderMock.mockReturnValue(
      new Promise((resolve) => {
        resolveRequest = resolve;
      }),
    );
    render(<OrderLookup />);

    await user.click(screen.getByRole("button", { name: "查询订单" }));

    expect(screen.getByRole("textbox", { name: "订单 ID" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "查询订单" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("正在查询订单 order-demo-001");
    await user.click(screen.getByRole("button", { name: "查询订单" }));
    expect(getOrderMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveRequest?.(orderFixture);
    });
    expect(await screen.findByRole("heading", { name: "EC-20260810-001" })).toBeInTheDocument();
  });

  it("renders exact order data and preserves the backend shipment order", async () => {
    const user = userEvent.setup();
    getOrderMock.mockResolvedValue(orderFixture);
    render(<OrderLookup />);

    await user.click(screen.getByRole("button", { name: "查询订单" }));

    expect(await screen.findByText("CNY 299.00")).toBeInTheDocument();
    expect(screen.getByText("已发货")).toBeInTheDocument();
    expect(screen.getByText(/2026年8月10日/)).toBeInTheDocument();
    expect(screen.getByText("上海市")).toBeInTheDocument();
    expect(screen.getByText("杭州市")).toBeInTheDocument();

    const timeline = screen.getByRole("list", { name: "物流节点" });
    const firstEvent = within(timeline).getByText("包裹运输中");
    const secondEvent = within(timeline).getByText("商家已确认订单");
    expect(
      firstEvent.compareDocumentPosition(secondEvent) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it.each([
    ["order_not_found", "订单不存在。", 404],
    ["order_service_unavailable", "订单服务暂时不可用，请稍后重试。", 503],
    ["order_network_error", "网络连接失败，请检查连接后重试。", 0],
  ] as const)("shows the stable %s failure", async (code, message, status) => {
    const user = userEvent.setup();
    getOrderMock.mockRejectedValue(new OrderApiError(code, message, status));
    render(<OrderLookup />);

    await user.click(screen.getByRole("button", { name: "查询订单" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByRole("button", { name: "重试订单查询" })).toBeEnabled();
  });

  it("retries the last submitted order ID", async () => {
    const user = userEvent.setup();
    getOrderMock
      .mockRejectedValueOnce(new OrderApiError("order_not_found", "订单不存在。", 404))
      .mockResolvedValueOnce(orderFixture);
    render(<OrderLookup />);

    const input = screen.getByRole("textbox", { name: "订单 ID" });
    await user.clear(input);
    await user.type(input, "order-missing");
    await user.click(screen.getByRole("button", { name: "查询订单" }));
    await user.click(await screen.findByRole("button", { name: "重试订单查询" }));

    expect(getOrderMock).toHaveBeenNthCalledWith(1, "order-missing");
    expect(getOrderMock).toHaveBeenNthCalledWith(2, "order-missing");
    expect(await screen.findByRole("heading", { name: "EC-20260810-001" })).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("uses safe labels for unknown order and shipment statuses", async () => {
    const user = userEvent.setup();
    getOrderMock.mockResolvedValue({
      ...orderFixture,
      status: "held_for_review",
      shipment_events: [
        { ...orderFixture.shipment_events[0], status: "customs_pending" },
      ],
    });
    render(<OrderLookup />);

    await user.click(screen.getByRole("button", { name: "查询订单" }));

    expect(await screen.findByText("未知状态（held_for_review）")).toBeInTheDocument();
    expect(screen.getByText("未知状态（customs_pending）")).toBeInTheDocument();
  });

  it("removes nonessential entry motion when reduced motion is preferred", async () => {
    const user = userEvent.setup();
    useReducedMotionMock.mockReturnValue(true);
    getOrderMock.mockResolvedValue(orderFixture);
    render(<OrderLookup />);

    await user.click(screen.getByRole("button", { name: "查询订单" }));

    const detail = await screen.findByRole("article");
    expect(detail).toHaveAttribute("data-motion-mode", "reduced");
    await waitFor(() => expect(detail).toHaveStyle({ opacity: "1" }));
  });
});
