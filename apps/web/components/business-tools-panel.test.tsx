import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { useReducedMotionMock } = vi.hoisted(() => ({
  useReducedMotionMock: vi.fn(),
}));

vi.mock("@/components/order-lookup", () => ({
  OrderLookup: () => <input aria-label="订单工具草稿" />,
}));

vi.mock("@/components/knowledge-search", () => ({
  KnowledgeSearch: () => <input aria-label="知识库工具草稿" />,
}));

vi.mock("@/components/refund-operations", () => ({
  RefundOperations: () => <div>退款运维内容</div>,
}));

vi.mock("motion/react", async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return { ...actual, useReducedMotion: useReducedMotionMock };
});

import { BusinessToolsPanel, type BusinessTool } from "./business-tools-panel";

function PanelHarness({ initiallyOpen = true }: { initiallyOpen?: boolean }) {
  const [activeTool, setActiveTool] = useState<BusinessTool>("order");
  const [isOpen, setIsOpen] = useState(initiallyOpen);

  return (
    <BusinessToolsPanel
      activeTool={activeTool}
      approvalDemoEnabled={false}
      isOpen={isOpen}
      onClose={() => setIsOpen(false)}
      onSelectTool={(tool) => {
        setActiveTool(tool);
        setIsOpen(true);
      }}
    />
  );
}

describe("BusinessToolsPanel", () => {
  beforeEach(() => {
    useReducedMotionMock.mockReset();
    useReducedMotionMock.mockReturnValue(false);
  });

  it("switches tabs without losing tool state", async () => {
    const user = userEvent.setup();
    render(<PanelHarness />);

    const orderTab = screen.getByRole("tab", { name: "订单查询" });
    const knowledgeTab = screen.getByRole("tab", { name: "知识库" });
    expect(orderTab).toHaveAttribute("aria-selected", "true");

    const orderDraft = screen.getByRole("textbox", { name: "订单工具草稿" });
    await user.type(orderDraft, "order-demo-001");
    await user.click(knowledgeTab);

    expect(knowledgeTab).toHaveAttribute("aria-selected", "true");
    expect(document.getElementById("order-tool-panel")).toHaveAttribute("hidden");
    expect(document.getElementById("knowledge-tool-panel")).not.toHaveAttribute("hidden");

    await user.click(orderTab);
    expect(screen.getByRole("textbox", { name: "订单工具草稿" })).toHaveValue(
      "order-demo-001",
    );
  });

  it("supports keyboard tab switching", async () => {
    const user = userEvent.setup();
    render(<PanelHarness />);

    const orderTab = screen.getByRole("tab", { name: "订单查询" });
    orderTab.focus();
    await user.keyboard("{ArrowRight}");

    expect(screen.getByRole("tab", { name: "知识库" })).toHaveFocus();
    expect(screen.getByRole("tab", { name: "知识库" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("uses an overlay drawer that does not participate in workspace layout", () => {
    render(<PanelHarness />);

    const panel = screen.getByRole("dialog", { name: "业务工具" });
    expect(panel).toHaveClass("absolute");
    expect(panel).not.toHaveClass("xl:relative");
    expect(panel).toHaveAttribute("aria-modal", "true");
  });

  it("closes the drawer with Escape at every viewport size", async () => {
    const user = userEvent.setup();
    render(<PanelHarness />);

    await user.keyboard("{Escape}");

    expect(document.getElementById("business-tools-panel")).toHaveAttribute(
      "aria-hidden",
      "true",
    );
    expect(screen.queryByRole("dialog", { name: "业务工具" })).not.toBeInTheDocument();
  });

  it("moves focus into the drawer and returns it to the opener when closed", async () => {
    function FocusHarness() {
      const [isOpen, setIsOpen] = useState(false);

      return (
        <>
          <button onClick={() => setIsOpen(true)} type="button">
            打开工具
          </button>
          <BusinessToolsPanel
            activeTool="order"
            approvalDemoEnabled={false}
            isOpen={isOpen}
            onClose={() => setIsOpen(false)}
            onSelectTool={() => undefined}
          />
        </>
      );
    }

    const user = userEvent.setup();
    render(<FocusHarness />);

    const opener = screen.getByRole("button", { name: "打开工具" });
    await user.click(opener);
    expect(screen.getByRole("dialog", { name: "业务工具" })).toHaveFocus();

    await user.click(screen.getByRole("button", { name: "关闭业务工具面板" }));
    expect(opener).toHaveFocus();
  });

  it("exposes reduced-motion mode without removing tool content", () => {
    useReducedMotionMock.mockReturnValue(true);
    render(<PanelHarness />);

    expect(screen.getByRole("dialog", { name: "业务工具" })).toHaveAttribute(
      "data-motion-mode",
      "reduced",
    );
    expect(screen.getByRole("textbox", { name: "订单工具草稿" })).toBeInTheDocument();
  });

  it("shows refund operations only for an ADMIN", () => {
    const { rerender } = render(
      <BusinessToolsPanel
        activeTool="order"
        approvalDemoEnabled={false}
        isOpen
        onClose={() => undefined}
        onSelectTool={() => undefined}
        userRole="SUPPORT"
      />,
    );
    expect(screen.queryByRole("tab", { name: "退款运维" })).not.toBeInTheDocument();

    rerender(
      <BusinessToolsPanel
        activeTool="refund-operations"
        approvalDemoEnabled
        isOpen
        onClose={() => undefined}
        onSelectTool={() => undefined}
        userRole="ADMIN"
      />,
    );
    expect(screen.getByRole("tab", { name: "退款运维" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("退款运维内容")).toBeInTheDocument();
  });
});
