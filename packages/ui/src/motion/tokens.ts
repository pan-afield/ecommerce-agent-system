import type { Transition, Variants } from "motion/react";

const productiveEase = [0.2, 0, 0, 1] as const;

export const motionTokens = {
  duration: {
    immediate: 0.08,
    fast: 0.14,
    standard: 0.2,
    deliberate: 0.28,
  },
  distance: {
    feedback: 3,
    enter: 6,
  },
  ease: {
    productive: productiveEase,
  },
} as const;

export const motionTransitions = {
  feedback: {
    duration: motionTokens.duration.fast,
    ease: productiveEase,
  } satisfies Transition,
  enter: {
    duration: motionTokens.duration.standard,
    ease: productiveEase,
  } satisfies Transition,
  layout: {
    duration: motionTokens.duration.standard,
    ease: productiveEase,
  } satisfies Transition,
} as const;

export const motionVariants = {
  page: {
    hidden: { opacity: 0, y: motionTokens.distance.enter },
    visible: { opacity: 1, y: 0, transition: motionTransitions.enter },
  },
  append: {
    hidden: { opacity: 0, y: motionTokens.distance.feedback },
    visible: { opacity: 1, y: 0, transition: motionTransitions.feedback },
  },
  context: {
    hidden: { opacity: 0 },
    visible: { opacity: 1, transition: motionTransitions.enter },
  },
  surface: {
    hidden: { opacity: 0, scale: 0.99 },
    visible: { opacity: 1, scale: 1, transition: motionTransitions.enter },
  },
  status: {
    hidden: { opacity: 0, scale: 0.97 },
    visible: { opacity: 1, scale: 1, transition: motionTransitions.feedback },
  },
} satisfies Record<string, Variants>;

export type MotionPreset = keyof typeof motionVariants;
