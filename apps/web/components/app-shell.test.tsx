import { act, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { AppShell } from "./app-shell";
import { AUTH_SESSION_EXPIRED_EVENT } from "@/lib/authenticated-fetch";
import { REFUND_SESSION_STORAGE_KEY } from "@/lib/refund-session";

describe("AppShell", () => {
  afterEach(() => window.sessionStorage.clear());

  it("renders the service workspace navigation and welcome state", () => {
    render(<AppShell />);

    expect(screen.getByRole("heading", { name: "客服工作台" })).toBeInTheDocument();
    expect(screen.getAllByLabelText("Relay Desk")).toHaveLength(2);

    const navigation = screen.getByRole("navigation", { name: "Primary navigation" });
    expect(within(navigation).getByText("客服工作台")).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(navigation).getByText("能力配置")).not.toHaveAttribute("aria-current");
    expect(screen.getByText("今天需要处理什么问题？")).toBeInTheDocument();
    expect(screen.getByText("已认证会话")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "当前用户" })).toHaveTextContent("demo@example.com");
    expect(screen.getByRole("button", { name: "退出登录" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "账户" })).toBeInTheDocument();
    expect(screen.queryByText("Signed in as")).toBeInTheDocument();
  });

  it("starts with an empty disabled composer", () => {
    render(<AppShell />);

    expect(screen.getByRole("textbox", { name: "输入消息" })).toHaveValue("");
    expect(screen.getByRole("button", { name: "发送消息" })).toBeDisabled();
    expect(screen.getByText("0 / 2000")).toBeInTheDocument();
  });

  it("returns to the login screen after refresh failure invalidates the session", () => {
    window.sessionStorage.setItem(REFUND_SESSION_STORAGE_KEY, "customer-refund");
    render(<AppShell />);

    act(() => window.dispatchEvent(new Event(AUTH_SESSION_EXPIRED_EVENT)));

    expect(screen.getByRole("heading", { name: "登录客服工作台" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "客服工作台" })).not.toBeInTheDocument();
    expect(window.sessionStorage.getItem(REFUND_SESSION_STORAGE_KEY)).toBeNull();
  });
});
