import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { sendChatMessageMock, useReducedMotionMock } = vi.hoisted(() => ({
  sendChatMessageMock: vi.fn(),
  useReducedMotionMock: vi.fn(),
}));

vi.mock("@/lib/chat-api", async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return { ...actual, sendChatMessage: sendChatMessageMock };
});

vi.mock("motion/react", async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return { ...actual, useReducedMotion: useReducedMotionMock };
});

import { ChatApiError } from "@/lib/chat-api";

import { ChatWorkspace } from "./chat-workspace";

describe("ChatWorkspace", () => {
  beforeEach(() => {
    sendChatMessageMock.mockReset();
    useReducedMotionMock.mockReset();
    useReducedMotionMock.mockReturnValue(false);
  });

  it("sends a trimmed message and appends the assistant response", async () => {
    const user = userEvent.setup();
    sendChatMessageMock.mockResolvedValue({
      assistant: { content: "您好，我可以帮您处理问题。" },
      model: "test-model",
    });
    render(<ChatWorkspace />);

    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "  你好  ");
    await user.click(screen.getByRole("button", { name: "发送消息" }));

    expect(sendChatMessageMock).toHaveBeenCalledWith("你好");
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
    expect(sendChatMessageMock).not.toHaveBeenCalled();
  });

  it("uses Enter to send and Shift+Enter to insert a line break", async () => {
    const user = userEvent.setup();
    sendChatMessageMock.mockResolvedValue({
      assistant: { content: "已收到。" },
      model: "test-model",
    });
    render(<ChatWorkspace />);

    const input = screen.getByRole("textbox", { name: "输入消息" });
    await user.type(input, "第一行");
    await user.keyboard("{Shift>}{Enter}{/Shift}第二行");

    expect(input).toHaveValue("第一行\n第二行");
    expect(sendChatMessageMock).not.toHaveBeenCalled();

    await user.keyboard("{Enter}");
    expect(sendChatMessageMock).toHaveBeenCalledWith("第一行\n第二行");
  });

  it("disables the composer and prevents duplicate submits while loading", async () => {
    const user = userEvent.setup();
    let resolveRequest: ((value: { assistant: { content: string }; model: string }) => void) | null =
      null;
    sendChatMessageMock.mockReturnValue(
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
    expect(screen.getByRole("status")).toHaveTextContent("正在等待回复");
    await user.keyboard("{Enter}");
    expect(sendChatMessageMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveRequest?.({ assistant: { content: "完成" }, model: "test-model" });
    });
    expect(await screen.findByText("完成")).toBeInTheDocument();
  });

  it("shows a stable provider error and keeps the user message", async () => {
    const user = userEvent.setup();
    sendChatMessageMock.mockRejectedValue(
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
    sendChatMessageMock.mockRejectedValue(
      new ChatApiError("chat_network_error", "网络连接失败，请检查连接后重试。", 0),
    );
    render(<ChatWorkspace />);

    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "网络测试");
    await user.click(screen.getByRole("button", { name: "发送消息" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "网络连接失败，请检查连接后重试。",
    );
  });

  it("retries the failed message without removing or duplicating it", async () => {
    const user = userEvent.setup();
    sendChatMessageMock
      .mockRejectedValueOnce(new ChatApiError("chat_timeout", "响应超时。", 504))
      .mockResolvedValueOnce({
        assistant: { content: "重试成功。" },
        model: "test-model",
      });
    render(<ChatWorkspace />);

    await user.type(screen.getByRole("textbox", { name: "输入消息" }), "同一条消息");
    await user.click(screen.getByRole("button", { name: "发送消息" }));
    await user.click(await screen.findByRole("button", { name: "重试这条消息" }));

    expect(sendChatMessageMock).toHaveBeenNthCalledWith(1, "同一条消息");
    expect(sendChatMessageMock).toHaveBeenNthCalledWith(2, "同一条消息");
    expect(await screen.findByText("重试成功。")).toBeInTheDocument();
    expect(screen.getAllByText("同一条消息")).toHaveLength(1);
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("uses instant scrolling when reduced motion is preferred", async () => {
    const user = userEvent.setup();
    useReducedMotionMock.mockReturnValue(true);
    sendChatMessageMock.mockResolvedValue({
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
  });
});
