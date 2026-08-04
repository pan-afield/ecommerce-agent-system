import type { HTMLAttributes, ReactNode } from "react";

import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { useReducedMotionMock } = vi.hoisted(() => ({
  useReducedMotionMock: vi.fn(),
}));

vi.mock("motion/react", () => ({
  motion: {
    div: ({
      animate,
      initial,
      variants: _variants,
      ...props
    }: HTMLAttributes<HTMLDivElement> & {
      animate?: string;
      children?: ReactNode;
      initial?: false | string;
      variants?: unknown;
    }) => (
      <div
        data-animate={animate}
        data-has-variants={_variants ? "true" : "false"}
        data-initial={initial === false ? "false" : initial}
        {...props}
      />
    ),
  },
  useReducedMotion: useReducedMotionMock,
}));

import { MotionReveal } from "./motion-reveal";

describe("MotionReveal", () => {
  beforeEach(() => {
    useReducedMotionMock.mockReset();
  });

  it("uses the hidden state when motion is allowed", () => {
    useReducedMotionMock.mockReturnValue(false);

    const { container } = render(<MotionReveal>Content</MotionReveal>);

    expect(container.firstChild).toHaveAttribute("data-initial", "hidden");
    expect(container.firstChild).toHaveAttribute("data-animate", "visible");
  });

  it("skips the initial animation when reduced motion is preferred", () => {
    useReducedMotionMock.mockReturnValue(true);

    const { container } = render(<MotionReveal>Content</MotionReveal>);

    expect(container.firstChild).toHaveAttribute("data-initial", "false");
    expect(container.firstChild).toHaveAttribute("data-animate", "visible");
  });
});
