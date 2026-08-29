"use client";

import { motion, useReducedMotion } from "motion/react";
import { ChevronDown, FileText, Hash, MapPin } from "lucide-react";

import { motionTransitions, motionVariants } from "@ecommerce-agent-system/ui";
import type { KnowledgeCitation } from "@/types/rag";

interface KnowledgeEvidenceProps {
  citations: KnowledgeCitation[];
  compact?: boolean;
}

export function KnowledgeEvidence({ citations, compact = false }: KnowledgeEvidenceProps) {
  const reduceMotion = useReducedMotion() ?? false;

  if (citations.length === 0) {
    return null;
  }

  return (
    <section
      aria-label="知识库证据"
      className={`${compact ? "mt-3" : "mt-4"} overflow-hidden rounded-md border border-line bg-canvas`}
      data-motion-mode={reduceMotion ? "reduced" : "standard"}
    >
      <div className="flex items-center justify-between border-b border-line px-3.5 py-2.5">
        <div className="flex items-center gap-2">
          <FileText className="size-3.5 text-accent" aria-hidden="true" />
          <h4 className="text-xs font-bold text-ink">知识库证据</h4>
        </div>
        <span className="font-mono text-[10px] text-ink-muted">{citations.length} SOURCES</span>
      </div>
      <ol className="divide-y divide-line">
        {citations.map((citation, index) => (
          <motion.li
            animate="visible"
            className="px-3.5 py-3"
            initial={reduceMotion ? false : "hidden"}
            key={citation.chunk_id}
            transition={
              reduceMotion
                ? { duration: 0 }
                : { ...motionTransitions.feedback, delay: Math.min(index * 0.03, 0.12) }
            }
            variants={motionVariants.append}
          >
            <details>
              <summary className="flex cursor-pointer list-none items-start gap-2 text-left [&::-webkit-details-marker]:hidden">
                <ChevronDown className="mt-0.5 size-3.5 shrink-0 text-ink-muted" aria-hidden="true" />
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs font-semibold text-ink">
                    <span className="inline-flex min-w-0 items-center gap-1">
                      <FileText className="size-3 text-ink-muted" aria-hidden="true" />
                      <span className="break-all">{citation.source_id}</span>
                    </span>
                    {citation.page_number !== null && (
                      <span className="inline-flex items-center gap-1 font-mono text-[10px] font-normal text-ink-muted">
                        <MapPin className="size-3" aria-hidden="true" />
                        第 {citation.page_number} 页
                      </span>
                    )}
                  </span>
                  <span className="mt-1 block line-clamp-2 text-xs leading-5 text-ink-muted">
                    {citation.content}
                  </span>
                </span>
              </summary>
              <div className="ml-5 mt-2 space-y-2 border-l-2 border-line-strong pl-3 text-xs text-ink-muted">
                <p className="whitespace-pre-wrap leading-5">{citation.content}</p>
                <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-[10px]">
                  <span className="inline-flex items-center gap-1">
                    <Hash className="size-3" aria-hidden="true" />
                    {citation.chunk_id}
                  </span>
                  <span>相关度 {citation.score.toFixed(4)}</span>
                </div>
              </div>
            </details>
          </motion.li>
        ))}
      </ol>
    </section>
  );
}
