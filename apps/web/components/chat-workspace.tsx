"use client";

import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";

import {
  Badge,
  Button,
  MotionReveal,
  motionTransitions,
  motionVariants,
} from "@ecommerce-agent-system/ui";
import {
  AlertCircle,
  BookOpen,
  Bot,
  LoaderCircle,
  MessageSquarePlus,
  MessageSquareText,
  PackageCheck,
  RotateCcw,
  Send,
  ShieldAlert,
  X,
} from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";

import {
  BusinessToolsPanel,
  type BusinessTool,
} from "@/components/business-tools-panel";
import { OrderDetailCard } from "@/components/order-detail-card";
import { RefundFlow } from "@/components/refund-flow";
import { KnowledgeEvidence } from "@/components/knowledge-evidence";
import { ChatApiError, streamChatMessage } from "@/lib/chat-api";
import type { AuthUser } from "@/lib/auth-client";
import {
  clearChatSession,
  createChatIdentifier,
  loadChatSession,
  saveChatSession,
} from "@/lib/chat-session";
import { getOrder } from "@/lib/order-api";
import {
  DEFAULT_RATE_LIMIT_RETRY_AFTER_SECONDS,
  formatRateLimitMessage,
} from "@/lib/retry-after";
import {
  CHAT_MESSAGE_MAX_LENGTH,
  type ChatErrorDetail,
  type ChatStreamPhase,
  type LocalChatMessage,
} from "@/types/chat";

function countCharacters(value: string) {
  return Array.from(value).length;
}

const streamPhaseLabels: Record<ChatStreamPhase, string> = {
  connecting: "正在连接客服服务",
  processing: "正在处理请求",
  finalizing: "正在整理回复",
};

function findExplicitOrderId(value: string) {
  return value.match(/\border-[a-z0-9][a-z0-9_-]{0,57}\b/i)?.[0] ?? null;
}

function normalizeClientError(error: unknown): ChatErrorDetail {
  if (error instanceof ChatApiError) {
    return { code: error.code, message: error.message };
  }

  return {
    code: "chat_network_error",
    message: "网络连接失败，请检查连接后重试。",
  };
}

interface ChatMessageItemProps {
  cooldownSeconds: number;
  isBusy: boolean;
  message: LocalChatMessage;
  onRetry: (message: LocalChatMessage) => void;
  reduceMotion: boolean;
}

function ChatMessageItem({
  cooldownSeconds,
  isBusy,
  message,
  onRetry,
  reduceMotion,
}: ChatMessageItemProps) {
  const isUser = message.role === "user";
  const isRateLimited = message.error?.code === "chat_rate_limited";
  const errorMessage = isRateLimited
    ? cooldownSeconds > 0
      ? formatRateLimitMessage(cooldownSeconds)
      : "请求限制已解除，可以重试。"
    : message.error?.message;

  return (
    <motion.li
      animate="visible"
      className={`flex ${isUser ? "justify-end" : "justify-start"}`}
      initial={reduceMotion ? false : "hidden"}
      layout="position"
      transition={{ layout: motionTransitions.layout }}
      variants={motionVariants.append}
    >
      <div
        className={`flex flex-col ${
          isUser
            ? "max-w-[88%] items-end sm:max-w-[76%]"
            : message.order
              ? "w-full max-w-full items-start sm:max-w-[88%]"
              : "max-w-[88%] items-start sm:max-w-[76%]"
        }`}
      >
        {!isUser && (
          <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-ink-muted">
            <span className="grid size-6 place-items-center rounded-md bg-accent-soft text-accent">
              <Bot className="size-3.5" aria-hidden="true" />
            </span>
            Relay Assistant
          </div>
        )}

        <div
          className={`whitespace-pre-wrap break-words rounded-md px-4 py-3 text-sm leading-6 ${
            isUser
              ? "bg-ink text-white"
              : "border border-line bg-surface-raised text-ink shadow-[0_1px_2px_rgba(23,33,29,0.04)]"
          }`}
        >
          {message.content}
        </div>

        {!isUser && message.model && (
          <p className="mt-1.5 px-1 font-mono text-[10px] text-ink-muted">{message.model}</p>
        )}

        {!isUser && message.order && (
          <div className="w-full min-w-0">
            <OrderDetailCard
              compact
              order={message.order}
              reduceMotion={reduceMotion}
            />
            <RefundFlow
              approvalDemoEnabled={false}
              order={message.order}
              reduceMotion={reduceMotion}
              sessionScope={`chat-message:${message.id}`}
            />
          </div>
        )}

        {!isUser && message.citations && message.citations.length > 0 && (
          <div className="w-full min-w-0">
            <KnowledgeEvidence citations={message.citations} compact />
          </div>
        )}

        <AnimatePresence initial={false}>
          {message.state === "failed" && message.error && (
            <motion.div
              animate="visible"
              className="mt-2 flex items-start gap-2 rounded-md border border-accent/30 bg-accent-soft p-3 text-accent"
              exit={reduceMotion ? undefined : "hidden"}
              initial={reduceMotion ? false : "hidden"}
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
                      ? `${cooldownSeconds} 秒后可重试这条消息`
                      : "重试这条消息"
                  }
                  className="mt-2 h-8 border-accent/30 bg-transparent px-2.5 text-xs text-accent hover:border-accent hover:bg-surface"
                  disabled={isBusy || cooldownSeconds > 0}
                  onClick={() => onRetry(message)}
                  variant="secondary"
                >
                  <RotateCcw className="size-3.5" aria-hidden="true" />
                  {cooldownSeconds > 0 ? `${cooldownSeconds} 秒后重试` : "重试"}
                </Button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </motion.li>
  );
}

interface ChatWorkspaceProps {
  approvalDemoEnabled?: boolean;
  userRole?: AuthUser["role"];
}

export function ChatWorkspace({ approvalDemoEnabled = false, userRole = "CUSTOMER" }: ChatWorkspaceProps) {
  const [messages, setMessages] = useState<LocalChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [activeRequestId, setActiveRequestId] = useState<string | null>(null);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [sessionReady, setSessionReady] = useState(false);
  const [sessionAnnouncement, setSessionAnnouncement] = useState("");
  const [streamPhase, setStreamPhase] = useState<ChatStreamPhase | null>(null);
  const [cooldownSeconds, setCooldownSeconds] = useState(0);
  const [activeTool, setActiveTool] = useState<BusinessTool>("order");
  const [toolsOpen, setToolsOpen] = useState(false);
  const [toolAnnouncement, setToolAnnouncement] = useState("");
  const requestInFlight = useRef(false);
  const activeAbortController = useRef<AbortController | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const shouldReduceMotion = useReducedMotion() ?? false;

  const trimmedDraft = draft.trim();
  const draftLength = countCharacters(draft);
  const canSubmit =
    sessionReady &&
    Boolean(threadId) &&
    !activeRequestId &&
    cooldownSeconds === 0 &&
    trimmedDraft.length > 0 &&
    countCharacters(trimmedDraft) <= CHAT_MESSAGE_MAX_LENGTH;

  useEffect(() => {
    const restoredSession = loadChatSession(window.sessionStorage);
    // Session storage is client-only, so restoration must follow server hydration.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setThreadId(restoredSession?.threadId ?? createChatIdentifier("thread"));
    setMessages(restoredSession?.messages ?? []);
    setSessionReady(true);
  }, []);

  useEffect(() => {
    if (!sessionReady || threadId === null) {
      return;
    }

    saveChatSession(window.sessionStorage, { threadId, messages });
  }, [messages, sessionReady, threadId]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({
      behavior: shouldReduceMotion ? "auto" : "smooth",
      block: "end",
    });
  }, [activeRequestId, messages, shouldReduceMotion]);

  useEffect(() => {
    if (cooldownSeconds === 0) {
      return;
    }

    const timeoutId = window.setTimeout(() => {
      setCooldownSeconds((current) => Math.max(0, current - 1));
    }, 1_000);
    return () => window.clearTimeout(timeoutId);
  }, [cooldownSeconds]);

  async function requestReply(userMessage: LocalChatMessage) {
    if (
      requestInFlight.current ||
      cooldownSeconds > 0 ||
      threadId === null ||
      !userMessage.requestId
    ) {
      return;
    }

    requestInFlight.current = true;
    const abortController = new AbortController();
    activeAbortController.current = abortController;
    setStreamPhase("connecting");
    setActiveRequestId(userMessage.id);
    setMessages((current) =>
      current.map((message) =>
        message.id === userMessage.id
          ? { ...message, state: "pending", error: undefined }
          : message,
      ),
    );

    try {
      const response = await streamChatMessage(
        {
          message: userMessage.content,
          thread_id: threadId,
          request_id: userMessage.requestId,
        },
        {
          signal: abortController.signal,
          onPhaseChange: setStreamPhase,
        },
      );
      const orderId = findExplicitOrderId(userMessage.content);
      let order: LocalChatMessage["order"];
      if (orderId !== null) {
        try {
          order = await getOrder(orderId);
        } catch {
          // The assistant response remains authoritative when structured lookup is unavailable.
        }
      }
      const assistantMessage: LocalChatMessage = {
        id: createChatIdentifier("message"),
        role: "assistant",
        content: response.assistant.content,
        model: response.model,
        ...(response.citations === undefined ? {} : { citations: response.citations }),
        ...(orderId === null ? {} : { orderId }),
        ...(order === undefined ? {} : { order }),
        state: "sent",
      };

      setMessages((current) => [
        ...current.map((message) =>
          message.id === userMessage.id ? { ...message, state: "sent" as const } : message,
        ),
        assistantMessage,
      ]);
    } catch (error) {
      if (error instanceof ChatApiError && error.code === "chat_rate_limited") {
        setCooldownSeconds(
          error.retryAfterSeconds ?? DEFAULT_RATE_LIMIT_RETRY_AFTER_SECONDS,
        );
      }
      const chatError = normalizeClientError(error);
      setMessages((current) =>
        current.map((message) =>
          message.id === userMessage.id
            ? { ...message, state: "failed", error: chatError }
            : message,
        ),
      );
    } finally {
      requestInFlight.current = false;
      activeAbortController.current = null;
      setStreamPhase(null);
      setActiveRequestId(null);
    }
  }

  function submitDraft() {
    if (!canSubmit || requestInFlight.current) {
      return;
    }

    const userMessage: LocalChatMessage = {
      id: createChatIdentifier("message"),
      role: "user",
      content: trimmedDraft,
      requestId: createChatIdentifier("request"),
      state: "pending",
    };

    setMessages((current) => [...current, userMessage]);
    setDraft("");
    void requestReply(userMessage);
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    submitDraft();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (
      event.key === "Enter" &&
      !event.shiftKey &&
      !event.nativeEvent.isComposing
    ) {
      event.preventDefault();
      submitDraft();
    }
  }

  function handleDraftChange(value: string) {
    if (countCharacters(value) <= CHAT_MESSAGE_MAX_LENGTH) {
      setDraft(value);
    }
  }

  function startNewSession() {
    if (requestInFlight.current) {
      return;
    }

    clearChatSession(window.sessionStorage);
    setThreadId(createChatIdentifier("thread"));
    setMessages([]);
    setDraft("");
    setSessionAnnouncement((current) =>
      current === "已开始新会话。" ? "新的会话已就绪。" : "已开始新会话。",
    );
  }

  function cancelReply() {
    activeAbortController.current?.abort();
  }

  function openTool(tool: BusinessTool) {
    setActiveTool(tool);
    setToolsOpen(true);
    setToolAnnouncement(
      tool === "order"
        ? "已打开订单查询。"
        : tool === "knowledge"
          ? "已打开知识库检索。"
          : "已打开退款运维。",
    );
  }

  function closeTools() {
    setToolsOpen(false);
    setToolAnnouncement("已关闭业务工具。");
  }

  function toggleTool(tool: BusinessTool) {
    if (toolsOpen && activeTool === tool) {
      closeTools();
      return;
    }

    openTool(tool);
  }

  return (
    <div className="relative flex min-h-0 min-w-0 flex-1 overflow-hidden bg-surface">
      <main
        aria-labelledby="workspace-title"
        className="flex min-h-0 min-w-0 flex-1 flex-col bg-surface"
        inert={toolsOpen}
      >
      <header className="flex min-h-16 shrink-0 items-center justify-between border-b border-line px-5 sm:px-8">
        <div>
          <p className="font-mono text-[10px] uppercase text-ink-muted">Workspace / V1.0</p>
          <h1 id="workspace-title" className="text-base font-bold text-ink">
            客服工作台
          </h1>
        </div>
        <div className="flex items-center gap-2">
          <AnimatePresence initial={false} mode="wait">
            <motion.div
              animate="visible"
              className="hidden sm:block"
              data-motion-mode={shouldReduceMotion ? "reduced" : "standard"}
              initial={shouldReduceMotion ? false : "hidden"}
              key={activeRequestId ? "busy" : `ready-${threadId ?? "pending"}`}
              variants={motionVariants.context}
            >
              <Badge>
                <span
                  className={`mr-1.5 size-1.5 rounded-full ${
                    activeRequestId ? "bg-accent" : "bg-positive"
                  }`}
                  aria-hidden="true"
                />
                {activeRequestId ? "正在响应" : "已认证会话"}
              </Badge>
            </motion.div>
          </AnimatePresence>
          <Button
            aria-controls="business-tools-panel"
            aria-expanded={toolsOpen && activeTool === "order"}
            aria-label={toolsOpen && activeTool === "order" ? "关闭订单查询" : "打开订单查询"}
            className="size-9 px-0"
            onClick={() => toggleTool("order")}
            title="订单查询"
            variant={toolsOpen && activeTool === "order" ? "secondary" : "ghost"}
          >
            <PackageCheck className="size-4" aria-hidden="true" />
          </Button>
          <Button
            aria-controls="business-tools-panel"
            aria-expanded={toolsOpen && activeTool === "knowledge"}
            aria-label={toolsOpen && activeTool === "knowledge" ? "关闭知识库检索" : "打开知识库检索"}
            className="size-9 px-0"
            onClick={() => toggleTool("knowledge")}
            title="知识库检索"
            variant={toolsOpen && activeTool === "knowledge" ? "secondary" : "ghost"}
          >
            <BookOpen className="size-4" aria-hidden="true" />
          </Button>
          {userRole === "ADMIN" && (
            <Button
              aria-controls="business-tools-panel"
              aria-expanded={toolsOpen && activeTool === "refund-operations"}
              aria-label={toolsOpen && activeTool === "refund-operations" ? "关闭退款运维" : "打开退款运维"}
              className="size-9 px-0"
              onClick={() => toggleTool("refund-operations")}
              title="退款运维"
              variant={toolsOpen && activeTool === "refund-operations" ? "secondary" : "ghost"}
            >
              <ShieldAlert className="size-4" aria-hidden="true" />
            </Button>
          )}
          <span className="mx-0.5 hidden h-5 w-px bg-line sm:block" aria-hidden="true" />
          <Button
            aria-label="开始新会话"
            className="size-9 px-0"
            disabled={Boolean(activeRequestId) || !sessionReady}
            onClick={startNewSession}
            title="开始新会话"
            variant="ghost"
          >
            <MessageSquarePlus className="size-4" aria-hidden="true" />
          </Button>
          <span className="sr-only" aria-live="polite">
            {sessionAnnouncement}
          </span>
          <span className="sr-only" aria-live="polite">
            {toolAnnouncement}
          </span>
        </div>
      </header>

      <section className="min-h-0 flex-1 overflow-y-auto" aria-label="消息记录">
        {messages.length === 0 ? (
          <div className="mx-auto flex min-h-full max-w-3xl items-center px-5 py-10 sm:px-8">
            <MotionReveal className="w-full">
              <div className="border-y border-line py-10 sm:py-12">
                <span className="mb-6 grid size-11 place-items-center rounded-md border border-line-strong bg-surface-raised text-accent">
                  <MessageSquareText className="size-5" aria-hidden="true" />
                </span>
                <p className="mb-3 font-mono text-xs uppercase text-accent">Ready / New message</p>
                <h2 className="max-w-xl text-3xl font-bold leading-tight text-ink-strong sm:text-4xl">
                  今天需要处理什么问题？
                </h2>
                <p className="mt-4 max-w-xl text-sm leading-6 text-ink-muted sm:text-base">
                  会话上下文按当前身份隔离；订单问题可由订单 Agent 安全查询。
                </p>
              </div>
            </MotionReveal>
          </div>
        ) : (
          <ol className="mx-auto flex w-full max-w-4xl flex-col gap-5 px-5 py-8 sm:px-8">
            {messages.map((message) => (
              <ChatMessageItem
                cooldownSeconds={cooldownSeconds}
                isBusy={Boolean(activeRequestId)}
                key={message.id}
                message={message}
                onRetry={(failedMessage) => void requestReply(failedMessage)}
                reduceMotion={shouldReduceMotion}
              />
            ))}

            <AnimatePresence initial={false}>
              {activeRequestId && (
                <motion.li
                  animate="visible"
                  aria-live="polite"
                  className="flex justify-start"
                  exit={shouldReduceMotion ? undefined : "hidden"}
                  initial={shouldReduceMotion ? false : "hidden"}
                  role="status"
                  variants={motionVariants.status}
                >
                  <div className="flex items-center gap-3 rounded-md border border-line bg-surface-raised px-4 py-3 text-sm text-ink-muted">
                    <LoaderCircle className="size-4 animate-spin text-accent" aria-hidden="true" />
                    <span>{streamPhase ? streamPhaseLabels[streamPhase] : "正在等待回复"}</span>
                    <Button
                      aria-label="取消回复"
                      className="ml-1 h-8 px-2 text-xs"
                      onClick={cancelReply}
                      variant="ghost"
                    >
                      <X className="size-3.5" aria-hidden="true" />
                      取消
                    </Button>
                  </div>
                </motion.li>
              )}
            </AnimatePresence>
          </ol>
        )}
        <div ref={messagesEndRef} />
      </section>

      <footer className="shrink-0 border-t border-line bg-surface-raised px-4 py-3 sm:px-8 sm:py-4">
        <form className="mx-auto max-w-4xl" onSubmit={handleSubmit}>
          <div className="rounded-md border border-line-strong bg-surface shadow-[0_1px_2px_rgba(23,33,29,0.04)] transition-colors focus-within:border-ink-muted focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-accent">
            <label className="sr-only" htmlFor="chat-message">
              输入消息
            </label>
            <textarea
              aria-describedby="chat-message-hint"
              className="block max-h-36 min-h-16 w-full resize-none bg-transparent px-4 py-3 text-sm leading-6 text-ink outline-none placeholder:text-ink-muted/70 disabled:cursor-wait disabled:opacity-60"
              disabled={Boolean(activeRequestId) || !sessionReady}
              id="chat-message"
              maxLength={CHAT_MESSAGE_MAX_LENGTH}
              onChange={(event) => handleDraftChange(event.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="输入消息…"
              rows={2}
              value={draft}
            />
            <div className="flex min-h-11 items-center justify-between border-t border-line px-3 py-2">
              <p aria-live="polite" className="text-xs text-ink-muted" id="chat-message-hint">
                {cooldownSeconds > 0
                  ? `服务冷却中 · ${cooldownSeconds} 秒后可发送`
                  : "Enter 发送 · Shift+Enter 换行"}
              </p>
              <div className="flex items-center gap-3">
                <span className="font-mono text-[10px] text-ink-muted">
                  {draftLength} / {CHAT_MESSAGE_MAX_LENGTH}
                </span>
                <Button aria-label="发送消息" disabled={!canSubmit} type="submit">
                  {activeRequestId ? (
                    <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
                  ) : (
                    <Send className="size-4" aria-hidden="true" />
                  )}
                  发送
                </Button>
              </div>
            </div>
          </div>
        </form>
      </footer>
      </main>

      <BusinessToolsPanel
        activeTool={activeTool}
        approvalDemoEnabled={approvalDemoEnabled}
        isOpen={toolsOpen}
        onClose={closeTools}
        onSelectTool={openTool}
        userRole={userRole}
      />
    </div>
  );
}
