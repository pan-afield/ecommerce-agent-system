import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AppShell } from "./app-shell";

describe("AppShell", () => {
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
    expect(screen.getByText("单轮模式")).toBeInTheDocument();
  });

  it("starts with an empty disabled composer", () => {
    render(<AppShell />);

    expect(screen.getByRole("textbox", { name: "输入消息" })).toHaveValue("");
    expect(screen.getByRole("button", { name: "发送消息" })).toBeDisabled();
    expect(screen.getByText("0 / 2000")).toBeInTheDocument();
  });
});
