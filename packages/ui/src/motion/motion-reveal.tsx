"use client";

import { motion, useReducedMotion } from "motion/react";
import type { ReactNode } from "react";

import { cn } from "../lib/cn";
import { motionVariants, type MotionPreset } from "./tokens";

export interface MotionRevealProps {
  children: ReactNode;
  className?: string;
  preset?: MotionPreset;
}

export function MotionReveal({
  children,
  className,
  preset = "page",
}: MotionRevealProps) {
  const shouldReduceMotion = useReducedMotion();

  return (
    <motion.div
      animate="visible"
      className={cn(className)}
      initial={shouldReduceMotion ? false : "hidden"}
      variants={motionVariants[preset]}
    >
      {children}
    </motion.div>
  );
}
