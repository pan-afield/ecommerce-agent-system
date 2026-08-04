import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { AppShell } from "./app-shell";

describe("AppShell", () => {
  it("renders the workspace navigation and current environment state", () => {
    render(<AppShell />);

    expect(screen.getByRole("heading", { name: "Service operations" })).toBeInTheDocument();
    expect(screen.getAllByLabelText("Relay Desk")).toHaveLength(2);

    const navigation = screen.getByRole("navigation", { name: "Primary navigation" });
    expect(within(navigation).getByText("Workspace")).toHaveAttribute("aria-current", "page");
    expect(within(navigation).getByText("Capabilities")).not.toHaveAttribute("aria-current");
    expect(screen.getByText("Nothing is active yet.")).toBeInTheDocument();
    expect(screen.getByText("Local build")).toBeInTheDocument();
  });

  it("keeps the unavailable action disabled and out of the tab order", async () => {
    const user = userEvent.setup();
    render(<AppShell />);

    const action = screen.getByRole("button", { name: "No actions available" });
    expect(action).toBeDisabled();

    await user.tab();
    expect(action).not.toHaveFocus();
  });
});
