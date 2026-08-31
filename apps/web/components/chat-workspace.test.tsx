import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { getOrderMock, streamChatMessageMock, useReducedMotionMock } = vi.hoisted(() => ({
  getOrderMock: vi.fn(),
  streamChatMessageMock: vi.fn(),
  useReducedMotionMock: vi.fn(),
}));

vi.mock("@/lib/chat-api", async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return { ...actual, streamChatMessage: streamChatMessageMock };
});

vi.mock("@/lib/order-api", async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return { ...actual, getOrder: getOrderMock };
});

vi.mock("motion/react", async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return { ...actual, useReducedMotion: useReducedMotionMock };
});

import { ChatApiError } from "@/lib/chat-api";
import { CHAT_SESSION_STORAGE_KEY } from "@/lib/chat-session";
import type { OrderDetail } from "@/types/order";

import { ChatWorkspace } from "./chat-workspace";

const orderFixture: OrderDetail = {
  id: "order-demo-001",
  order_number: "EC-20260810-001",
  status: "shipped",
  total_amount: "299.00",
  currency: "CNY",
  created_at: "2026-08-10T08:30:00Z",
  shipment_events: [
    {
      id: "shipment-event-001",
      status: "confirmed",
      description: "商家已确认订单",
      location: "杭州市",
      occurred_at: "2026-08-08T01:15:00Z",
    },
    {
      id: "shipment-event-002",
      status: "in_transit",
      description: "包裹运输中",
      location: "上海市",
      occurred_at: "2026-08-10T03:20:00Z",
    },
  ],
};

describe("ChatWorkspace", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    getOrderMock.mockReset();
    streamChatMessageMock.mockReset();
    useReducedMotionMock.mockReset();
    useReducedMotionMock.mockReturnValue(false);
  });

  it("keeps business tools outside the independently scrolling chat main area", async () => {
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    const chatMain = screen.getByRole("main", { name: "客服工作台" });
    expect(within(chatMain).queryByRole("heading", { name: "订单查询" })).not.toBeInTheDocument();
    expect(within(chatMain).queryByRole("heading", { name: "知识库检索" })).not.toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "业务工具" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "打开知识库检索" }));
    expect(screen.getByRole("tab", { name: "知识库" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByText("已打开知识库检索。")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "关闭业务工具面板" }));
    expect(screen.getByText("已关闭业务工具。")).toBeInTheDocument();
  });

  it("sends a trimmed message and appends the assistant response", async () => {
    const user = userEvent.setup();
    streamChatMessageMock.mockResolvedValue({
      assistant: { content: "您好，我可以帮您处理问题。" },
      model: "test-model",
    });
    render(<ChatWorkspace />);

    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "  你好  ");
    await user.click(screen.getByRole("button", { name: "发送消息" }));

    expect(streamChatMessageMock).toHaveBeenCalledWith(
      {
        message: "你好",
        thread_id: expect.stringMatching(/^thread-/),
        request_id: expect.stringMatching(/^request-/),
      },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(screen.getByText("你好")).toBeInTheDocument();
    const assistantMessage = await screen.findByText("您好，我可以帮您处理问题。");
    expect(assistantMessage).toBeInTheDocument();
    await waitFor(() => {
      expect(assistantMessage.closest("li")).toHaveStyle({ opacity: "1" });
    });
    expect(screen.getByText("test-model")).toBeInTheDocument();
  });

  it("does not submit an empty or whitespace-only message", async () => {
    const user = userEvent.setup();
    render(<ChatWorkspace />);

    const input = screen.getByRole("textbox", { name: "输入消息" });
    await user.type(input, "   ");

    expect(screen.getByRole("button", { name: "发送消息" })).toBeDisabled();
    await user.keyboard("{Enter}");
    expect(streamChatMessageMock).not.toHaveBeenCalled();
  });

  it("uses Enter to send and Shift+Enter to insert a line break", async () => {
    const user = userEvent.setup();
    streamChatMessageMock.mockResolvedValue({
      assistant: { content: "已收到。" },
      model: "test-model",
    });
    render(<ChatWorkspace />);

    const input = screen.getByRole("textbox", { name: "输入消息" });
    await user.type(input, "第一行");
    await user.keyboard("{Shift>}{Enter}{/Shift}第二行");

    expect(input).toHaveValue("第一行\n第二行");
    expect(streamChatMessageMock).not.toHaveBeenCalled();

    await user.keyboard("{Enter}");
    expect(streamChatMessageMock).toHaveBeenCalledWith(
      {
        message: "第一行\n第二行",
        thread_id: expect.stringMatching(/^thread-/),
        request_id: expect.stringMatching(/^request-/),
      },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("disables the composer and prevents duplicate submits while loading", async () => {
    const user = userEvent.setup();
    let resolveRequest: ((value: { assistant: { content: string }; model: string }) => void) | null =
      null;
    streamChatMessageMock.mockReturnValue(
      new Promise((resolve) => {
        resolveRequest = resolve;
      }),
    );
    render(<ChatWorkspace />);

    const input = screen.getByRole("textbox", { name: "输入消息" });
    await user.type(input, "测试加载状态");
    await user.click(screen.getByRole("button", { name: "发送消息" }));

    expect(input).toBeDisabled();
    expect(screen.getByRole("button", { name: "发送消息" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("正在连接客服服务");
    await user.keyboard("{Enter}");
    expect(streamChatMessageMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveRequest?.({ assistant: { content: "完成" }, model: "test-model" });
    });
    expect(await screen.findByText("完成")).toBeInTheDocument();
  });

  it("announces stream phase changes and lets the user cancel and retry", async () => {
    const user = userEvent.setup();
    streamChatMessageMock
      .mockImplementationOnce(
        (_payload, options: { signal: AbortSignal; onPhaseChange: (phase: string) => void }) =>
          new Promise((_resolve, reject) => {
            options.onPhaseChange("processing");
            options.signal.addEventListener("abort", () => {
              reject(new ChatApiError("chat_cancelled", "已取消本次回复。", 0));
            });
          }),
      )
      .mockResolvedValueOnce({
        assistant: { content: "重试后完成。" },
        model: "test-model",
      });
    render(<ChatWorkspace />);

    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "取消测试");
    await user.click(screen.getByRole("button", { name: "发送消息" }));

    expect(await screen.findByRole("status")).toHaveTextContent("正在处理请求");
    await user.click(screen.getByRole("button", { name: "取消回复" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("已取消本次回复。");
    await user.click(screen.getByRole("button", { name: "重试这条消息" }));
    expect(await screen.findByText("重试后完成。")).toBeInTheDocument();
    expect(streamChatMessageMock.mock.calls[1]?.[0]).toEqual(
      streamChatMessageMock.mock.calls[0]?.[0],
    );
  });

  it("shows a stable provider error and keeps the user message", async () => {
    const user = userEvent.setup();
    streamChatMessageMock.mockRejectedValue(
      new ChatApiError("chat_rate_limited", "请求过于频繁，请稍后重试。", 429),
    );
    render(<ChatWorkspace />);

    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "请回答");
    await user.click(screen.getByRole("button", { name: "发送消息" }));

    expect(screen.getByText("请回答")).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent("请求过于频繁，请稍后重试。");
    expect(screen.getByRole("button", { name: "重试这条消息" })).toBeEnabled();
  });

  it("shows a stable message for browser network errors", async () => {
    const user = userEvent.setup();
    streamChatMessageMock.mockRejectedValue(
      new ChatApiError("chat_network_error", "网络连接失败，请检查连接后重试。", 0),
    );
    render(<ChatWorkspace />);

    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "网络测试");
    await user.click(screen.getByRole("button", { name: "发送消息" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "网络连接失败，请检查连接后重试。",
    );
  });

  it("shows a sanitized authentication error with retry", async () => {
    const user = userEvent.setup();
    streamChatMessageMock.mockRejectedValue(
      new ChatApiError("chat_unauthorized", "登录状态无效，请重新登录。", 401),
    );
    render(<ChatWorkspace />);

    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "认证测试");
    await user.click(screen.getByRole("button", { name: "发送消息" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "登录状态无效，请重新登录。",
    );
    expect(screen.getByRole("button", { name: "重试这条消息" })).toBeEnabled();
  });

  it("renders an authenticated structured order card after an order Agent reply", async () => {
    const user = userEvent.setup();
    streamChatMessageMock.mockResolvedValue({
      assistant: { content: "订单已发货，以下是当前物流进度。" },
      model: "test-model",
    });
    getOrderMock.mockResolvedValue(orderFixture);
    render(<ChatWorkspace />);

    await user.type(
      screen.getByRole("textbox", { name: "输入消息" }),
      "查询 order-demo-001",
    );
    await user.click(screen.getByRole("button", { name: "发送消息" }));

    expect(await screen.findByText("订单已发货，以下是当前物流进度。")).toBeInTheDocument();
    expect(getOrderMock).toHaveBeenCalledWith("order-demo-001");
    const card = screen.getByRole("article", { name: "EC-20260810-001" });
    expect(card).toHaveTextContent("CNY 299.00");
    expect(card).toHaveTextContent("已发货");
    const events = screen.getByRole("list", { name: "物流节点" });
    expect(events.textContent?.indexOf("商家已确认订单")).toBeLessThan(
      events.textContent?.indexOf("包裹运输中") ?? -1,
    );
  });

  it("renders authoritative citations returned with a streamed assistant response", async () => {
    const user = userEvent.setup();
    streamChatMessageMock.mockResolvedValue({
      assistant: { content: "根据知识库，退款需要订单本人提交。" },
      model: "test-model",
      citations: [
        {
          source_id: "refund-policy-v1",
          chunk_id: "c".repeat(64),
          page_number: 2,
          content: "退款需要订单本人提交。",
          score: 0.2,
        },
      ],
    });
    render(<ChatWorkspace />);

    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "退款政策");
    await user.click(screen.getByRole("button", { name: "发送消息" }));

    expect(await screen.findByRole("region", { name: "知识库证据" })).toHaveTextContent(
      "refund-policy-v1",
    );
    expect(screen.getByText("根据知识库，退款需要订单本人提交。")).toBeInTheDocument();
  });

  it("does not infer structured orders from assistant prose", async () => {
    const user = userEvent.setup();
    streamChatMessageMock.mockResolvedValue({
      assistant: { content: "请提供订单编号，例如 order-demo-001。" },
      model: "test-model",
    });
    render(<ChatWorkspace />);

    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "查询订单");
    await user.click(screen.getByRole("button", { name: "发送消息" }));

    await screen.findByText("请提供订单编号，例如 order-demo-001。");
    expect(getOrderMock).not.toHaveBeenCalled();
  });

  it("retries the failed message without removing or duplicating it", async () => {
    const user = userEvent.setup();
    streamChatMessageMock
      .mockRejectedValueOnce(new ChatApiError("chat_timeout", "响应超时。", 504))
      .mockResolvedValueOnce({
        assistant: { content: "重试成功。" },
        model: "test-model",
      });
    render(<ChatWorkspace />);

    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "同一条消息");
    await user.click(screen.getByRole("button", { name: "发送消息" }));
    await user.click(await screen.findByRole("button", { name: "重试这条消息" }));

    const firstRequest = streamChatMessageMock.mock.calls[0]?.[0];
    const retriedRequest = streamChatMessageMock.mock.calls[1]?.[0];
    expect(firstRequest).toMatchObject({ message: "同一条消息" });
    expect(retriedRequest).toEqual(firstRequest);
    expect(await screen.findByText("重试成功。")).toBeInTheDocument();
    expect(screen.getAllByText("同一条消息")).toHaveLength(1);
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("uses instant scrolling when reduced motion is preferred", async () => {
    const user = userEvent.setup();
    useReducedMotionMock.mockReturnValue(true);
    streamChatMessageMock.mockResolvedValue({
      assistant: { content: "无动画回复。" },
      model: "test-model",
    });
    render(<ChatWorkspace />);

    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "减少动态效果");
    await user.click(screen.getByRole("button", { name: "发送消息" }));
    await screen.findByText("无动画回复。");

    await waitFor(() => {
      expect(Element.prototype.scrollIntoView).toHaveBeenLastCalledWith({
        behavior: "auto",
        block: "end",
      });
    });

    const sessionStatus = screen.getByText("已认证会话").parentElement;
    expect(sessionStatus).toHaveAttribute("data-motion-mode", "reduced");
    expect(sessionStatus).toHaveStyle({ opacity: "1" });
  });

  it("disables order card movement when reduced motion is preferred", async () => {
    const user = userEvent.setup();
    useReducedMotionMock.mockReturnValue(true);
    streamChatMessageMock.mockResolvedValue({
      assistant: { content: "已找到订单。" },
      model: "test-model",
    });
    getOrderMock.mockResolvedValue(orderFixture);
    render(<ChatWorkspace />);

    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "order-demo-001");
    await user.click(screen.getByRole("button", { name: "发送消息" }));

    const card = await screen.findByRole("article", { name: "EC-20260810-001" });
    expect(card).toHaveAttribute("data-motion-mode", "reduced");
    expect(card).toHaveStyle({ opacity: "1" });
  });

  it("reuses one thread and creates a new request ID for each message", async () => {
    const user = userEvent.setup();
    streamChatMessageMock
      .mockResolvedValueOnce({
        assistant: { content: "第一条回复" },
        model: "test-model",
      })
      .mockResolvedValueOnce({
        assistant: { content: "第二条回复" },
        model: "test-model",
      });
    render(<ChatWorkspace />);

    const input = screen.getByRole("textbox", { name: "输入消息" });
    await user.type(input, "第一条消息");
    await user.click(screen.getByRole("button", { name: "发送消息" }));
    await screen.findByText("第一条回复");
    await user.type(input, "第二条消息");
    await user.click(screen.getByRole("button", { name: "发送消息" }));
    await screen.findByText("第二条回复");

    const firstRequest = streamChatMessageMock.mock.calls[0]?.[0];
    const secondRequest = streamChatMessageMock.mock.calls[1]?.[0];
    expect(firstRequest.thread_id).toBe(secondRequest.thread_id);
    expect(firstRequest.request_id).not.toBe(secondRequest.request_id);
  });

  it("restores the tab session and continues with the persisted thread", async () => {
    const user = userEvent.setup();
    window.sessionStorage.setItem(
      CHAT_SESSION_STORAGE_KEY,
      JSON.stringify({
        threadId: "thread-restored",
        messages: [
          {
            id: "message-user-restored",
            role: "user",
            content: "之前的问题",
            requestId: "request-restored",
            state: "sent",
          },
          {
            id: "message-assistant-restored",
            role: "assistant",
            content: "之前的回复",
            model: "test-model",
            state: "sent",
          },
        ],
      }),
    );
    streamChatMessageMock.mockResolvedValue({
      assistant: { content: "已结合上下文回答" },
      model: "test-model",
    });
    render(<ChatWorkspace />);

    expect(await screen.findByText("之前的问题")).toBeInTheDocument();
    expect(screen.getByText("之前的回复")).toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "继续");
    await user.click(screen.getByRole("button", { name: "发送消息" }));

    expect(streamChatMessageMock).toHaveBeenCalledWith(
      {
        message: "继续",
        thread_id: "thread-restored",
        request_id: expect.stringMatching(/^request-/),
      },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("starts an isolated new session and announces the state change", async () => {
    const user = userEvent.setup();
    streamChatMessageMock.mockResolvedValue({
      assistant: { content: "第一段会话回复" },
      model: "test-model",
    });
    render(<ChatWorkspace />);

    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "第一段会话");
    await user.click(screen.getByRole("button", { name: "发送消息" }));
    await screen.findByText("第一段会话回复");
    const firstThreadId = streamChatMessageMock.mock.calls[0]?.[0].thread_id;

    await user.click(screen.getByRole("button", { name: "开始新会话" }));

    expect(screen.queryByText("第一段会话")).not.toBeInTheDocument();
    expect(screen.getByText("已开始新会话。")).toBeInTheDocument();
    expect(screen.getByText("今天需要处理什么问题？")).toBeInTheDocument();

    streamChatMessageMock.mockResolvedValue({
      assistant: { content: "第二段会话回复" },
      model: "test-model",
    });
    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "第二段会话");
    await user.click(screen.getByRole("button", { name: "发送消息" }));
    await screen.findByText("第二段会话回复");

    expect(streamChatMessageMock.mock.calls[1]?.[0].thread_id).not.toBe(firstThreadId);
  });

  it("disables session reset while a request is in flight", async () => {
    const user = userEvent.setup();
    streamChatMessageMock.mockReturnValue(new Promise(() => undefined));
    render(<ChatWorkspace />);

    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "处理中");
    await user.click(screen.getByRole("button", { name: "发送消息" }));

    expect(screen.getByRole("button", { name: "开始新会话" })).toBeDisabled();
  });
});
