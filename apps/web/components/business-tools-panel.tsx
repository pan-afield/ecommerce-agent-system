"use client";

import { useEffect, useRef, type KeyboardEvent } from "react";

import { Button } from "@ecommerce-agent-system/ui";
import { BookOpen, PackageCheck, SlidersHorizontal, X } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";

import { KnowledgeSearch } from "@/components/knowledge-search";
import { OrderLookup } from "@/components/order-lookup";

export type BusinessTool = "knowledge" | "order";

interface BusinessToolsPanelProps {
  activeTool: BusinessTool;
  approvalDemoEnabled: boolean;
  isOpen: boolean;
  onClose: () => void;
  onSelectTool: (tool: BusinessTool) => void;
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
}: BusinessToolsPanelProps) {
  const reduceMotion = useReducedMotion() ?? false;
  const orderTabRef = useRef<HTMLButtonElement>(null);
  const knowledgeTabRef = useRef<HTMLButtonElement>(null);
  const tabRefs = { knowledge: knowledgeTabRef, order: orderTabRef };

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    function handleEscape(event: globalThis.KeyboardEvent) {
      if (
        event.key === "Escape" &&
        !window.matchMedia("(min-width: 1280px)").matches
      ) {
        onClose();
      }
    }

    window.addEventListener("keydown", handleEscape);
    return () => window.removeEventListener("keydown", handleEscape);
  }, [isOpen, onClose]);

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
    const nextTool =
      event.key === "ArrowLeft" || event.key === "Home" ? "order" : "knowledge";
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
            className="absolute inset-0 z-30 bg-ink/25 xl:hidden"
            exit={reduceMotion ? undefined : { opacity: 0 }}
            initial={reduceMotion ? false : { opacity: 0 }}
            onClick={onClose}
            type="button"
          />
        )}
      </AnimatePresence>

      <aside
        aria-labelledby="business-tools-title"
        className={`${isOpen ? "flex" : "hidden"} absolute inset-y-0 right-0 z-40 w-full min-w-0 flex-col border-l border-line bg-surface-raised shadow-shell sm:w-[28rem] xl:relative xl:z-auto xl:flex xl:w-[26rem] xl:shrink-0 xl:shadow-none`}
        data-motion-mode={reduceMotion ? "reduced" : "standard"}
        id="business-tools-panel"
      >
        <header className="flex min-h-16 shrink-0 items-center justify-between border-b border-line px-4">
          <div className="min-w-0">
            <p className="font-mono text-[10px] uppercase text-accent">Operations / V0.6</p>
            <h2 className="mt-0.5 flex items-center gap-2 text-sm font-bold text-ink" id="business-tools-title">
              <SlidersHorizontal className="size-4" aria-hidden="true" />
              业务工具
            </h2>
          </div>
          <Button
            aria-label="关闭业务工具面板"
            className="size-9 px-0 xl:hidden"
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
            className="grid grid-cols-2 gap-1 rounded-md bg-canvas p-1"
            role="tablist"
          >
            {tools.map(({ icon: Icon, id, label }) => {
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
        </div>
      </aside>
    </>
  );
}
