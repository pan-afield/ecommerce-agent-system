"use client";

import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";

import { Button, motionVariants } from "@ecommerce-agent-system/ui";
import { AlertCircle, BookOpen, LoaderCircle, RotateCcw, Search } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";

import { KnowledgeEvidence } from "@/components/knowledge-evidence";
import { RagApiError, searchKnowledge } from "@/lib/rag-api";
import {
  DEFAULT_RATE_LIMIT_RETRY_AFTER_SECONDS,
  formatRateLimitMessage,
} from "@/lib/retry-after";
import { RAG_QUERY_MAX_LENGTH, type RagErrorDetail, type RagSearchResponse } from "@/types/rag";

function normalizeRagError(error: unknown): RagErrorDetail {
  if (error instanceof RagApiError) {
    return { code: error.code, message: error.message };
  }

  return {
    code: "rag_network_error",
    message: "知识库连接失败，请检查连接后重试。",
  };
}

export function KnowledgeSearch() {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<RagSearchResponse | null>(null);
  const [error, setError] = useState<RagErrorDetail | null>(null);
  const [activeQuery, setActiveQuery] = useState<string | null>(null);
  const [cooldownSeconds, setCooldownSeconds] = useState(0);
  const lastSubmittedQuery = useRef("");
  const requestInFlight = useRef(false);
  const shouldReduceMotion = useReducedMotion() ?? false;
  const normalizedQuery = query.trim();
  const canSubmit =
    normalizedQuery.length > 0 && !activeQuery && cooldownSeconds === 0;
  const errorMessage =
    error?.code === "rag_rate_limited"
      ? cooldownSeconds > 0
        ? formatRateLimitMessage(cooldownSeconds)
        : "请求限制已解除，可以重试。"
      : error?.message;

  useEffect(() => {
    if (cooldownSeconds === 0) {
      return;
    }

    const timeoutId = window.setTimeout(() => {
      setCooldownSeconds((current) => Math.max(0, current - 1));
    }, 1_000);
    return () => window.clearTimeout(timeoutId);
  }, [cooldownSeconds]);

  async function requestSearch(requestedQuery: string) {
    const normalized = requestedQuery.trim();
    if (!normalized || requestInFlight.current || cooldownSeconds > 0) {
      return;
    }

    requestInFlight.current = true;
    lastSubmittedQuery.current = normalized;
    setActiveQuery(normalized);
    setResult(null);
    setError(null);
    try {
      setResult(await searchKnowledge(normalized));
    } catch (searchError) {
      if (searchError instanceof RagApiError && searchError.code === "rag_rate_limited") {
        setCooldownSeconds(
          searchError.retryAfterSeconds ?? DEFAULT_RATE_LIMIT_RETRY_AFTER_SECONDS,
        );
      }
      setError(normalizeRagError(searchError));
    } finally {
      requestInFlight.current = false;
      setActiveQuery(null);
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (canSubmit) {
      void requestSearch(normalizedQuery);
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter" && !event.nativeEvent.isComposing) {
      event.preventDefault();
      if (canSubmit) {
        void requestSearch(normalizedQuery);
      }
    }
  }

  return (
    <section className="min-w-0" aria-labelledby="knowledge-search-title">
      <div className="p-4">
        <div>
          <p className="font-mono text-[10px] uppercase text-accent">Evidence / V0.6</p>
          <h2 className="mt-1 flex items-center gap-2 text-sm font-bold text-ink" id="knowledge-search-title">
            <BookOpen className="size-4" aria-hidden="true" />
            知识库检索
          </h2>
          <form className="mt-3 flex w-full gap-2" onSubmit={handleSubmit}>
            <div className="min-w-0 flex-1">
              <label className="sr-only" htmlFor="knowledge-query">知识库查询</label>
              <input
                aria-describedby="knowledge-query-hint"
                className="h-9 w-full rounded-md border border-line-strong bg-surface px-3 text-xs text-ink outline-none transition-colors placeholder:text-ink-muted/70 focus:border-ink-muted focus:outline-2 focus:outline-offset-2 focus:outline-accent disabled:cursor-wait disabled:opacity-60"
                disabled={Boolean(activeQuery)}
                id="knowledge-query"
                maxLength={RAG_QUERY_MAX_LENGTH}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="例如：退款政策"
                value={query}
              />
            </div>
            <Button aria-label="检索知识库" disabled={!canSubmit} type="submit">
              {activeQuery ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <Search className="size-4" aria-hidden="true" />}
              检索
            </Button>
          </form>
        </div>
        <p className="mt-2 text-[10px] text-ink-muted" id="knowledge-query-hint">
          只读检索 · 最多返回 3 条相关证据
        </p>

        <AnimatePresence initial={false} mode="wait">
          {activeQuery && (
            <motion.div
              animate="visible"
              aria-live="polite"
              className="mt-3 flex items-center gap-2 text-xs text-ink-muted"
              exit={shouldReduceMotion ? undefined : "hidden"}
              initial={shouldReduceMotion ? false : "hidden"}
              key="loading"
              role="status"
              variants={motionVariants.status}
            >
              <LoaderCircle className="size-3.5 animate-spin text-accent" aria-hidden="true" />
              正在检索知识库
            </motion.div>
          )}

          {!activeQuery && error && (
            <motion.div
              animate="visible"
              className="mt-3 flex items-start gap-2 rounded-md border border-accent/30 bg-accent-soft p-3 text-accent"
              exit={shouldReduceMotion ? undefined : "hidden"}
              initial={shouldReduceMotion ? false : "hidden"}
              key="error"
              role="alert"
              variants={motionVariants.status}
            >
              <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              <div className="min-w-0 flex-1">
                <p aria-live="polite" className="text-xs font-semibold leading-5">
                  {errorMessage}
                </p>
                <Button
                  aria-label={
                    cooldownSeconds > 0
                      ? `${cooldownSeconds} 秒后可重试知识库检索`
                      : "重试知识库检索"
                  }
                  className="mt-2 h-8 border-accent/30 bg-transparent px-2.5 text-xs text-accent hover:border-accent hover:bg-surface"
                  disabled={cooldownSeconds > 0}
                  onClick={() => void requestSearch(lastSubmittedQuery.current)}
                  variant="secondary"
                >
                  <RotateCcw className="size-3.5" aria-hidden="true" />
                  {cooldownSeconds > 0 ? `${cooldownSeconds} 秒后重试` : "重试"}
                </Button>
              </div>
            </motion.div>
          )}

          {!activeQuery && !error && result && (
            <motion.div
              animate="visible"
              aria-live="polite"
              className="mt-3"
              exit={shouldReduceMotion ? undefined : "hidden"}
              initial={shouldReduceMotion ? false : "hidden"}
              key={`result-${result.query}`}
              variants={motionVariants.context}
            >
              {result.citations.length > 0 ? (
                <KnowledgeEvidence citations={result.citations} compact />
              ) : (
                <p className="rounded-md bg-canvas px-3 py-3 text-xs text-ink-muted">
                  未检索到相关知识库证据，请换一种问法重试。
                </p>
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </section>
  );
}
