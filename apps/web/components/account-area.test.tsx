import { render, screen, waitForElementToBeRemoved } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AuthUser } from "@/lib/auth-client";

const { useReducedMotionMock } = vi.hoisted(() => ({ useReducedMotionMock: vi.fn() }));

vi.mock("motion/react", async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return { ...actual, useReducedMotion: useReducedMotionMock };
});

import { DesktopAccountArea, MobileAccountArea } from "./account-area";

const user: AuthUser = {
  id: "user-1",
  email: "support-agent-with-a-long-email-address@example.com",
  role: "SUPPORT",
};

const defaultProps = {
  error: null,
  isLoggingOut: false,
  onLogout: vi.fn(),
  onRetry: vi.fn(),
  user,
};

describe("account areas", () => {
  beforeEach(() => vi.clearAllMocks());

  it("keeps the desktop account at the bottom and offers a full email disclosure", async () => {
    const viewer = userEvent.setup();
    render(<DesktopAccountArea {...defaultProps} />);

    expect(screen.getByRole("region", { name: "当前用户" })).toHaveTextContent("SUPPORT");
    const emailDisclosure = screen.getByRole("button", {
      name: `查看完整邮箱 ${user.email}`,
    });
    expect(screen.getByRole("button", { name: `查看完整邮箱 ${user.email}` })).toBeInTheDocument();
    await viewer.click(emailDisclosure);
    expect(screen.getAllByText(user.email)).toHaveLength(2);
  });

  it("opens a mobile account dialog, traps focus, and returns focus on Escape", async () => {
    const viewer = userEvent.setup();
    render(<MobileAccountArea {...defaultProps} />);

    const trigger = screen.getByRole("button", { name: "账户" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    await viewer.click(trigger);

    const dialog = screen.getByRole("dialog", { name: "账户" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveTextContent(user.email);
    expect(dialog).toHaveTextContent("SUPPORT");
    expect(screen.getByRole("button", { name: "关闭账户面板" })).toHaveFocus();

    await viewer.keyboard("{Escape}");
    await waitForElementToBeRemoved(() => screen.queryByRole("dialog", { name: "账户" }));
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(trigger).toHaveFocus();
  });

  it("shows a nearby retry action and keeps logout disabled while pending", async () => {
    const onRetry = vi.fn();
    const { rerender } = render(
      <DesktopAccountArea {...defaultProps} error="退出登录失败，请重试。" onRetry={onRetry} />,
    );

    const retry = screen.getByRole("button", { name: "重试退出登录" });
    expect(screen.getByRole("alert")).toHaveTextContent("退出登录失败，请重试。");
    await userEvent.setup().click(retry);
    expect(onRetry).toHaveBeenCalledOnce();

    rerender(<DesktopAccountArea {...defaultProps} isLoggingOut />);
    expect(screen.getByRole("button", { name: "正在退出登录" })).toBeDisabled();
    expect(screen.getByText("正在退出登录")).toBeInTheDocument();
  });

  it("removes non-essential movement when reduced motion is preferred", async () => {
    useReducedMotionMock.mockReturnValue(true);
    const viewer = userEvent.setup();
    render(<MobileAccountArea {...defaultProps} />);

    await viewer.click(screen.getByRole("button", { name: "账户" }));
    expect(screen.getByRole("dialog", { name: "账户" })).toHaveAttribute(
      "data-motion-mode",
      "reduced",
    );
  });
});
