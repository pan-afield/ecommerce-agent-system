"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Badge, Button, motionVariants } from "@ecommerce-agent-system/ui";
import {
  AlertCircle,
  CheckCircle2,
  ChevronRight,
  ClipboardList,
  LoaderCircle,
  RefreshCw,
  RotateCcw,
  Send,
  ShieldAlert,
} from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";

import {
  getRefundOperation,
  getRefundOperations,
  RefundApiError,
  reviewRefundApplication,
  runRefundOperation,
} from "@/lib/refund-api";
import type {
  RefundApplication,
  RefundAuditEvent,
  RefundOperationAction,
  RefundOperationDetail,
  RefundOperationQueueItem,
  RefundReviewDecision,
} from "@/types/refund";

const actionLabels: Record<RefundOperationAction, string> = {
  resume: "恢复有限补查",
  resubmit: "原键重投",
  "acknowledge-conflict": "确认已核查矛盾",
};

function formatDateTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("zh-CN", {
        dateStyle: "short",
        timeStyle: "medium",
        timeZone: "Asia/Shanghai",
      }).format(date);
}

function errorMessage(error: unknown) {
  return error instanceof RefundApiError
    ? error.message
    : "网络连接失败，请检查连接后重试。";
}

function availableActions(detail: RefundOperationDetail): RefundOperationAction[] {
  const { execution, recovery } = detail;
  if (recovery?.status !== "MANUAL_REQUIRED") return [];
  if (
    recovery.last_error_code === "TERMINAL_CONFLICT" &&
    (execution.status === "SUCCEEDED" || execution.status === "FAILED")
  ) {
    return ["acknowledge-conflict"];
  }
  const actions: RefundOperationAction[] = [];
  if (execution.status === "RUNNING" || execution.status === "PROCESSING") {
    actions.push("resume");
  }
  if (execution.status === "RUNNING" && recovery.last_error_code === "NOT_FOUND") {
    actions.push("resubmit");
  }
  return actions;
}

function AuditEventRow({ event }: { event: RefundAuditEvent }) {
  return (
    <li className="border-l border-line-strong pb-4 pl-4 last:pb-0">
      <span className="absolute -ml-[1.16rem] mt-1 size-2 rounded-full bg-ink-muted" aria-hidden="true" />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-mono text-[11px] font-semibold text-ink">#{event.id} · {event.action}</p>
        <time className="font-mono text-[10px] text-ink-muted">{formatDateTime(event.created_at)}</time>
      </div>
      <p className="mt-1 text-xs leading-5 text-ink-muted">
        来源 {event.source}
        {event.from_status || event.to_status ? ` · ${event.from_status ?? "-"} → ${event.to_status ?? "-"}` : ""}
        {event.error_code ? ` · ${event.error_code}` : ""}
      </p>
      {event.note && <p className="mt-1 text-xs leading-5 text-ink">备注：{event.note}</p>}
    </li>
  );
}

export function RefundOperations() {
  const reduceMotion = useReducedMotion() ?? false;
  const [items, setItems] = useState<RefundOperationQueueItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<RefundOperationDetail | null>(null);
  const [loadingQueue, setLoadingQueue] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [busyAction, setBusyAction] = useState<RefundOperationAction | null>(null);
  const [action, setAction] = useState<RefundOperationAction | null>(null);
  const [note, setNote] = useState("");
  const [resubmitConfirmed, setResubmitConfirmed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [approvalId, setApprovalId] = useState("");
  const [approvalDecision, setApprovalDecision] = useState<RefundReviewDecision | null>(null);
  const [approvalNote, setApprovalNote] = useState("");
  const [approvalBusy, setApprovalBusy] = useState(false);
  const [approvalResult, setApprovalResult] = useState<RefundApplication | null>(null);
  const queueRequest = useRef(0);

  const loadQueue = useCallback(async () => {
    const requestId = ++queueRequest.current;
    setLoadingQueue(true);
    setError(null);
    try {
      const response = await getRefundOperations(50);
      if (requestId !== queueRequest.current) return;
      setItems(response.items);
    } catch (requestError) {
      if (requestId === queueRequest.current) setError(errorMessage(requestError));
    } finally {
      if (requestId === queueRequest.current) setLoadingQueue(false);
    }
  }, []);

  const loadDetail = useCallback(async (applicationId: string) => {
    setSelectedId(applicationId);
    setLoadingDetail(true);
    setError(null);
    setAction(null);
    try {
      setDetail(await getRefundOperation(applicationId, "0", 50));
    } catch (requestError) {
      setDetail(null);
      setError(errorMessage(requestError));
    } finally {
      setLoadingDetail(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    queueMicrotask(() => {
      if (active) void loadQueue();
    });
    return () => {
      active = false;
      queueRequest.current += 1;
    };
  }, [loadQueue]);

  async function loadMoreEvents() {
    if (!detail || !selectedId || loadingMore || detail.events.length === 0) return;
    setLoadingMore(true);
    setError(null);
    try {
      const next = await getRefundOperation(selectedId, detail.next_after_id, 50);
      setDetail({ ...next, events: [...detail.events, ...next.events] });
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setLoadingMore(false);
    }
  }

  async function submitAction() {
    if (!action || !selectedId || busyAction || note.trim().length < 1) return;
    setBusyAction(action);
    setError(null);
    try {
      await runRefundOperation(selectedId, action, note.trim());
      setAction(null);
      setNote("");
      setResubmitConfirmed(false);
      await Promise.all([loadQueue(), loadDetail(selectedId)]);
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusyAction(null);
    }
  }

  async function submitApproval() {
    const normalizedId = approvalId.trim();
    if (!normalizedId || !approvalDecision || approvalBusy) return;
    setApprovalBusy(true);
    setError(null);
    try {
      setApprovalResult(
        await reviewRefundApplication(normalizedId, approvalDecision, approvalNote.trim()),
      );
      setApprovalDecision(null);
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setApprovalBusy(false);
    }
  }

  const canSubmit =
    action !== null &&
    note.trim().length >= 1 &&
    Array.from(note.trim()).length <= 500 &&
    (action !== "resubmit" || resubmitConfirmed) &&
    busyAction === null;

  return (
    <section
      aria-labelledby="refund-operations-title"
      className="min-w-0 p-4"
      data-motion-mode={reduceMotion ? "reduced" : "standard"}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-mono text-[10px] uppercase text-accent">Admin operations / V1.0</p>
          <h2 className="mt-1 flex items-center gap-2 text-sm font-bold text-ink" id="refund-operations-title">
            <ShieldAlert className="size-4" aria-hidden="true" />
            退款人工队列
          </h2>
        </div>
        <Button aria-label="刷新退款人工队列" className="size-9 px-0" disabled={loadingQueue} onClick={() => void loadQueue()} title="刷新队列" variant="ghost">
          <RefreshCw className={`size-4 ${loadingQueue ? "animate-spin" : ""}`} aria-hidden="true" />
        </Button>
      </div>

      <div className="mt-4 border-y border-line py-4">
        <p className="text-xs font-bold text-ink">审批申请</p>
        <p className="mt-1 text-xs leading-5 text-ink-muted">
          输入客户提供的退款申请编号。审批只改变申请状态，不代表资金已退款。
        </p>
        <label className="mt-3 block text-xs font-semibold text-ink-muted">
          退款申请编号
          <input
            aria-label="待审批退款申请编号"
            className="mt-1.5 h-9 w-full rounded-md border border-line-strong bg-surface px-3 font-mono text-xs text-ink outline-none focus:outline-2 focus:outline-offset-2 focus:outline-accent"
            maxLength={128}
            onChange={(event) => { setApprovalId(event.target.value); setApprovalResult(null); }}
            value={approvalId}
          />
        </label>
        <label className="mt-3 block text-xs font-semibold text-ink-muted">
          审批备注（可选）
          <textarea
            aria-label="管理员审批备注"
            className="mt-1.5 min-h-16 w-full resize-y rounded-md border border-line-strong bg-surface px-3 py-2 text-sm text-ink outline-none focus:outline-2 focus:outline-offset-2 focus:outline-accent"
            maxLength={500}
            onChange={(event) => setApprovalNote(event.target.value)}
            value={approvalNote}
          />
        </label>
        {approvalDecision === null ? (
          <div className="mt-3 flex flex-wrap gap-2">
            <Button disabled={!approvalId.trim()} onClick={() => setApprovalDecision("APPROVED")} variant="secondary">准备批准</Button>
            <Button disabled={!approvalId.trim()} onClick={() => setApprovalDecision("REJECTED")} variant="secondary">准备拒绝</Button>
          </div>
        ) : (
          <div className="mt-3 border-l-2 border-accent pl-3">
            <p className="text-xs font-semibold text-ink">将{approvalDecision === "APPROVED" ? "批准" : "拒绝"}申请 {approvalId.trim()}</p>
            <div className="mt-2 flex gap-2">
              <Button disabled={approvalBusy} onClick={() => void submitApproval()}>
                {approvalBusy && <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />}
                确认{approvalDecision === "APPROVED" ? "批准" : "拒绝"}
              </Button>
              <Button disabled={approvalBusy} onClick={() => setApprovalDecision(null)} variant="ghost">取消</Button>
            </div>
          </div>
        )}
        {approvalResult && (
          <p aria-live="polite" className="mt-3 text-xs font-semibold text-positive" role="status">
            申请 {approvalResult.id} 已{approvalResult.status === "APPROVED" ? "批准" : "拒绝"}；客户刷新后可看到权威状态。
          </p>
        )}
      </div>

      {loadingQueue ? (
        <p aria-live="polite" className="mt-4 flex items-center gap-2 text-xs text-ink-muted" role="status">
          <LoaderCircle className="size-3.5 animate-spin text-accent" aria-hidden="true" /> 正在读取人工队列
        </p>
      ) : !error && items.length === 0 ? (
        <div className="mt-4 border-y border-line py-6 text-center">
          <CheckCircle2 className="mx-auto size-5 text-positive" aria-hidden="true" />
          <p className="mt-2 text-sm font-semibold text-ink">当前没有待人工处理项</p>
        </div>
      ) : (
        <ul className="mt-4 space-y-2" aria-label="退款人工处理项">
          {items.map((item) => (
            <li key={item.refund_application_id}>
              <button
                className="flex w-full items-center justify-between gap-3 rounded-md border border-line bg-surface px-3 py-3 text-left transition-colors hover:border-line-strong focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                onClick={() => void loadDetail(item.refund_application_id)}
                type="button"
              >
                <span className="min-w-0">
                  <span className="block break-all font-mono text-xs font-semibold text-ink">{item.refund_application_id}</span>
                  <span className="mt-1 block text-[11px] text-ink-muted">{item.execution_status} · {item.last_error_code ?? "待核查"} · 第 {item.attempts} 次</span>
                </span>
                <ChevronRight className="size-4 shrink-0 text-ink-muted" aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}

      <AnimatePresence initial={false} mode="wait">
        {loadingDetail && (
          <motion.p animate="visible" aria-live="polite" className="mt-5 flex items-center gap-2 border-t border-line pt-4 text-xs text-ink-muted" initial={reduceMotion ? false : "hidden"} key="detail-loading" role="status" variants={motionVariants.status}>
            <LoaderCircle className="size-3.5 animate-spin text-accent" aria-hidden="true" /> 正在读取执行与审计详情
          </motion.p>
        )}
        {!loadingDetail && detail && selectedId && (
          <motion.div animate="visible" className="mt-5 border-t border-line pt-4" initial={reduceMotion ? false : "hidden"} key={selectedId} variants={motionVariants.status}>
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <p className="break-all font-mono text-xs font-bold text-ink">{selectedId}</p>
                <p className="mt-1 text-xs text-ink-muted">{detail.execution.currency} {detail.execution.amount}</p>
              </div>
              <Badge>{detail.execution.status}</Badge>
            </div>

            {detail.recovery && (
              <dl className="mt-3 grid grid-cols-2 gap-px overflow-hidden rounded-md border border-line bg-line text-xs">
                <div className="bg-surface px-3 py-2"><dt className="text-ink-muted">补偿任务</dt><dd className="mt-1 font-semibold text-ink">{detail.recovery.status}</dd></div>
                <div className="bg-surface px-3 py-2"><dt className="text-ink-muted">错误码</dt><dd className="mt-1 break-all font-mono text-ink">{detail.recovery.last_error_code ?? "-"}</dd></div>
              </dl>
            )}

            {availableActions(detail).length > 0 && (
              <div className="mt-4">
                <p className="text-xs font-semibold text-ink">可用处置</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {availableActions(detail).map((candidate) => (
                    <Button key={candidate} onClick={() => { setAction(candidate); setNote(""); setResubmitConfirmed(false); }} variant="secondary">
                      {candidate === "resubmit" ? <Send className="size-4" aria-hidden="true" /> : <RotateCcw className="size-4" aria-hidden="true" />}
                      {actionLabels[candidate]}
                    </Button>
                  ))}
                </div>
              </div>
            )}

            {action && (
              <div className="mt-4 border-l-2 border-accent pl-3">
                <p className="text-xs font-bold text-ink">{actionLabels[action]}</p>
                <label className="mt-2 block text-xs font-semibold text-ink-muted">
                  处理备注（1～500 字符）
                  <textarea aria-label="退款运维备注" className="mt-1.5 min-h-20 w-full resize-y rounded-md border border-line-strong bg-canvas px-3 py-2 text-sm text-ink outline-none focus:outline-2 focus:outline-offset-2 focus:outline-accent" maxLength={500} onChange={(event) => setNote(event.target.value)} value={note} />
                </label>
                {action === "resubmit" && (
                  <label className="mt-3 flex items-start gap-2 text-xs leading-5 text-ink">
                    <input checked={resubmitConfirmed} className="mt-1" onChange={(event) => setResubmitConfirmed(event.target.checked)} type="checkbox" />
                    我已确认：仅以服务端原金额和原幂等键重投；未知结果不会创建新退款。
                  </label>
                )}
                {action === "acknowledge-conflict" && (
                  <p className="mt-2 text-xs leading-5 text-ink-muted">此操作只记录已人工核查，不会修改退款终态。</p>
                )}
                <div className="mt-3 flex gap-2">
                  <Button disabled={!canSubmit} onClick={() => void submitAction()}>
                    {busyAction ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : null}
                    确认{actionLabels[action]}
                  </Button>
                  <Button disabled={Boolean(busyAction)} onClick={() => setAction(null)} variant="ghost">取消</Button>
                </div>
              </div>
            )}

            <div className="mt-5">
              <h3 className="flex items-center gap-2 text-xs font-bold text-ink"><ClipboardList className="size-4" aria-hidden="true" />审计记录</h3>
              {detail.events.length === 0 ? (
                <p className="mt-3 text-xs text-ink-muted">当前页没有新的审计记录。</p>
              ) : (
                <ol className="relative mt-3 ml-1"><>{detail.events.map((event) => <AuditEventRow event={event} key={event.id} />)}</></ol>
              )}
              {detail.events.length >= 50 && (
                <Button className="mt-3" disabled={loadingMore} onClick={() => void loadMoreEvents()} variant="ghost">
                  {loadingMore && <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />}加载更多审计
                </Button>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {error && (
        <div className="mt-4 flex items-start gap-2 rounded-md border border-accent/30 bg-accent-soft p-3 text-accent" role="alert">
          <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <p className="text-xs font-semibold leading-5">{error}</p>
        </div>
      )}
    </section>
  );
}
