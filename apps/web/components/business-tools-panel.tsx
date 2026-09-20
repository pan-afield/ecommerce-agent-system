"use client";

import { useEffect, useRef, type KeyboardEvent } from "react";

import { Button, motionTransitions } from "@ecommerce-agent-system/ui";
import { BookOpen, PackageCheck, ShieldAlert, SlidersHorizontal, X } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";

import { KnowledgeSearch } from "@/components/knowledge-search";
import { OrderLookup } from "@/components/order-lookup";
import { RefundOperations } from "@/components/refund-operations";
import type { AuthUser } from "@/lib/auth-client";

export type BusinessTool = "knowledge" | "order" | "refund-operations";

interface BusinessToolsPanelProps {
  activeTool: BusinessTool;
  approvalDemoEnabled: boolean;
  isOpen: boolean;
  onClose: () => void;
  onSelectTool: (tool: BusinessTool) => void;
  userRole?: AuthUser["role"];
}

const tools: Array<{
  icon: typeof PackageCheck;
  id: BusinessTool;
  label: string;
}> = [
  { icon: PackageCheck, id: "order", label: "订单查询" },
  { icon: BookOpen, id: "knowledge", label: "知识库" },
];

export function BusinessToolsPanel({
  activeTool,
  approvalDemoEnabled,
  isOpen,
  onClose,
  onSelectTool,
  userRole = "CUSTOMER",
}: BusinessToolsPanelProps) {
  const reduceMotion = useReducedMotion() ?? false;
  const orderTabRef = useRef<HTMLButtonElement>(null);
  const knowledgeTabRef = useRef<HTMLButtonElement>(null);
  const operationsTabRef = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);
  const panelRef = useRef<HTMLElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const tabRefs = { knowledge: knowledgeTabRef, order: orderTabRef, "refund-operations": operationsTabRef };
  const visibleTools = userRole === "ADMIN"
    ? [...tools, { icon: ShieldAlert, id: "refund-operations" as const, label: "退款运维" }]
    : tools;

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    returnFocusRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;

    function handleEscape(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") {
        onCloseRef.current();
      }
    }

    panelRef.current?.focus();
    window.addEventListener("keydown", handleEscape);
    return () => {
      window.removeEventListener("keydown", handleEscape);
      returnFocusRef.current?.focus();
    };
  }, [isOpen]);

  function handleTabKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (![
      "ArrowLeft",
      "ArrowRight",
      "Home",
      "End",
    ].includes(event.key)) {
      return;
    }

    event.preventDefault();
    const currentIndex = visibleTools.findIndex((tool) => tool.id === activeTool);
    const nextIndex = event.key === "Home"
      ? 0
      : event.key === "End"
        ? visibleTools.length - 1
        : event.key === "ArrowLeft"
          ? (currentIndex - 1 + visibleTools.length) % visibleTools.length
          : (currentIndex + 1) % visibleTools.length;
    const nextTool = visibleTools[nextIndex]?.id ?? "order";
    onSelectTool(nextTool);
    tabRefs[nextTool].current?.focus();
  }

  return (
    <>
      <AnimatePresence initial={false}>
        {isOpen && (
          <motion.button
            animate={reduceMotion ? undefined : { opacity: 1 }}
            aria-label="关闭业务工具"
            className="absolute inset-0 z-30 bg-ink/25"
            exit={reduceMotion ? undefined : { opacity: 0 }}
            initial={reduceMotion ? false : { opacity: 0 }}
            onClick={onClose}
            transition={motionTransitions.feedback}
            type="button"
          />
        )}
      </AnimatePresence>

      <motion.aside
        animate={{
          opacity: isOpen ? 1 : 0,
          x: isOpen ? 0 : "100%",
        }}
        aria-hidden={!isOpen}
        aria-labelledby="business-tools-title"
        aria-modal={isOpen ? true : undefined}
        className={`absolute inset-y-0 right-0 z-40 flex w-full min-w-0 max-w-full flex-col border-l border-line bg-surface-raised shadow-shell sm:w-[30rem] lg:w-[32rem] ${
          isOpen ? "pointer-events-auto" : "pointer-events-none"
        }`}
        data-motion-mode={reduceMotion ? "reduced" : "standard"}
        id="business-tools-panel"
        inert={!isOpen}
        initial={false}
        ref={panelRef}
        role="dialog"
        tabIndex={-1}
        transition={reduceMotion ? { duration: 0 } : motionTransitions.enter}
      >
        <header className="flex min-h-16 shrink-0 items-center justify-between border-b border-line px-4">
          <div className="min-w-0">
            <p className="font-mono text-[10px] uppercase text-accent">Operations / V1.0</p>
            <h2 className="mt-0.5 flex items-center gap-2 text-sm font-bold text-ink" id="business-tools-title">
              <SlidersHorizontal className="size-4" aria-hidden="true" />
              业务工具
            </h2>
          </div>
          <Button
            aria-label="关闭业务工具面板"
            className="size-9 px-0"
            onClick={onClose}
            title="关闭业务工具"
            variant="ghost"
          >
            <X className="size-4" aria-hidden="true" />
          </Button>
        </header>

        <div className="shrink-0 border-b border-line p-3">
          <div
            aria-label="业务工具类型"
            className={`grid gap-1 rounded-md bg-canvas p-1 ${userRole === "ADMIN" ? "grid-cols-3" : "grid-cols-2"}`}
            role="tablist"
          >
            {visibleTools.map(({ icon: Icon, id, label }) => {
              const selected = activeTool === id;
              return (
                <button
                  aria-controls={`${id}-tool-panel`}
                  aria-selected={selected}
                  className={`inline-flex h-9 items-center justify-center gap-2 rounded-md px-3 text-xs font-semibold transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${
                    selected
                      ? "border border-line-strong bg-surface text-ink shadow-[0_1px_2px_rgba(23,33,29,0.06)]"
                      : "border border-transparent text-ink-muted hover:text-ink"
                  }`}
                  id={`${id}-tool-tab`}
                  key={id}
                  onClick={() => onSelectTool(id)}
                  onKeyDown={handleTabKeyDown}
                  ref={tabRefs[id]}
                  role="tab"
                  tabIndex={selected ? 0 : -1}
                  type="button"
                >
                  <Icon className="size-3.5" aria-hidden="true" />
                  {label}
                </button>
              );
            })}
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
          <div
            aria-labelledby="order-tool-tab"
            hidden={activeTool !== "order"}
            id="order-tool-panel"
            role="tabpanel"
            tabIndex={0}
          >
            <OrderLookup approvalDemoEnabled={approvalDemoEnabled} />
          </div>
          <div
            aria-labelledby="knowledge-tool-tab"
            hidden={activeTool !== "knowledge"}
            id="knowledge-tool-panel"
            role="tabpanel"
            tabIndex={0}
          >
            <KnowledgeSearch />
          </div>
          {userRole === "ADMIN" && (
            <div
              aria-labelledby="refund-operations-tool-tab"
              hidden={activeTool !== "refund-operations"}
              id="refund-operations-tool-panel"
              role="tabpanel"
              tabIndex={0}
            >
              <RefundOperations />
            </div>
          )}
        </div>
      </motion.aside>
    </>
  );
}
