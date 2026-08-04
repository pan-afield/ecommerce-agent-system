import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Badge } from "./badge";
import { Button } from "./button";

describe("UI primitives", () => {
  it("runs a button action with safe button semantics", async () => {
    const onClick = vi.fn();
    const user = userEvent.setup();

    render(<Button onClick={onClick}>Confirm</Button>);

    const button = screen.getByRole("button", { name: "Confirm" });
    expect(button).toHaveAttribute("type", "button");

    await user.click(button);
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("renders badge content without changing its semantics", () => {
    render(<Badge aria-label="Environment status">Local build</Badge>);

    expect(screen.getByLabelText("Environment status")).toHaveTextContent("Local build");
  });
});
