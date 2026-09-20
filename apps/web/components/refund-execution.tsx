"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Badge, Button, motionVariants } from "@ecommerce-agent-system/ui";
import {
  AlertCircle,
  CheckCircle2,
  CircleDashed,
  Clock3,
  LoaderCircle,
  RefreshCw,
  RotateCcw,
  Send,
  XCircle,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";

import {
  executeRefund,
  getRefundExecution,
  recoverRefundExecution,
  RefundApiError,
} from "@/lib/refund-api";
import type { RefundApplication, RefundExecution, RefundExecutionStatus } from "@/types/refund";

const labels: Record<RefundExecutionStatus, string> = {
  PENDING: "等待执行",
  RUNNING: "执行结果待确认",
  PROCESSING: "渠道处理中",
  SUCCEEDED: "退款执行成功",
  FAILED: "退款执行失败",
};

const descriptions: Record<RefundExecutionStatus, string> = {
  PENDING: "执行记录已经创建，尚未完成沙箱调用。请刷新状态，不要重复发起新退款。",
  RUNNING: "请求是否到达沙箱仍待确认。当前不是失败状态，请先查询或补查原退款键。",
  PROCESSING: "沙箱已受理，正在等待最终结果或签名回调。审批通过本身不代表退款完成。",
  SUCCEEDED: "服务端已收到权威成功结果，退款金额以执行记录为准。",
  FAILED: "服务端已收到权威失败结果。本界面不会自动重新提交退款。",
};

type Action = "execute" | "lookup" | "recover";

function isUnknownOutcome(error: unknown) {
  return (
    error instanceof RefundApiError &&
    [
      "refund_network_error",
      "refund_outcome_unknown",
      "refund_upstream_timeout",
      "refund_upstream_unreachable",
    ].includes(error.code)
  );
}

function messageOf(error: unknown) {
  return error instanceof RefundApiError
    ? error.message
    : "网络连接失败，请检查连接后重试。";
}

interface RefundExecutionPanelProps {
  application: RefundApplication;
  reduceMotion: boolean;
}

export function RefundExecutionPanel({ application, reduceMotion }: RefundExecutionPanelProps) {
  const [execution, setExecution] = useState<RefundExecution | null>(null);
  const [ready, setReady] = useState(false);
  const [busyAction, setBusyAction] = useState<Action | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [unknownOutcome, setUnknownOutcome] = useState(false);
  const [executeArmed, setExecuteArmed] = useState(false);
  const requestInFlight = useRef(false);

  const loadExecution = useCallback(async () => {
    if (requestInFlight.current) return;
    requestInFlight.current = true;
    setBusyAction("lookup");
    setError(null);
    try {
      setExecution(await getRefundExecution(application.id));
      setUnknownOutcome(false);
    } catch (lookupError) {
      if (lookupError instanceof RefundApiError && lookupError.status === 404) {
        setExecution(null);
        setUnknownOutcome(false);
      } else {
        setError(messageOf(lookupError));
      }
    } finally {
      requestInFlight.current = false;
      setBusyAction(null);
      setReady(true);
    }
  }, [application.id]);

  useEffect(() => {
    let active = true;
    queueMicrotask(() => {
      if (active) void loadExecution();
    });
    return () => {
      active = false;
    };
  }, [loadExecution]);

  async function runAction(action: "execute" | "recover") {
    if (requestInFlight.current) return;
    requestInFlight.current = true;
    setBusyAction(action);
    setError(null);
    try {
      const result =
        action === "execute"
          ? await executeRefund(application.id)
          : await recoverRefundExecution(application.id);
      setExecution(result);
      setUnknownOutcome(false);
      setExecuteArmed(false);
    } catch (actionError) {
      if (action === "execute" && isUnknownOutcome(actionError)) {
        setUnknownOutcome(true);
        setError("执行请求结果暂时未知。请先查询执行状态，不要重复提交退款。");
      } else {
        setError(messageOf(actionError));
      }
    } finally {
      requestInFlight.current = false;
      setBusyAction(null);
    }
  }

  const status = execution?.status;
  const statusIcon =
    status === "SUCCEEDED" ? CheckCircle2 : status === "FAILED" ? XCircle : status ? Clock3 : CircleDashed;
  const StatusIcon = statusIcon;

  return (
    <section
      aria-labelledby="refund-execution-title"
      className="mt-4 border-t border-line pt-4"
      data-motion-mode={reduceMotion ? "reduced" : "standard"}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="font-mono text-[10px] uppercase text-ink-muted">Sandbox execution / V1.0</p>
          <h4 className="mt-1 text-sm font-bold text-ink" id="refund-execution-title">
            资金执行状态
          </h4>
        </div>
        {status && <Badge>{labels[status]}</Badge>}
      </div>

      <AnimatePresence initial={false} mode="wait">
        {!ready && busyAction === "lookup" && (
          <motion.p
            animate="visible"
            aria-live="polite"
            className="mt-3 flex items-center gap-2 text-xs text-ink-muted"
            initial={reduceMotion ? false : "hidden"}
            key="execution-loading"
            role="status"
            variants={motionVariants.status}
          >
            <LoaderCircle className="size-3.5 animate-spin text-accent" aria-hidden="true" />
            正在读取退款执行状态
          </motion.p>
        )}

        {ready && execution && (
          <motion.div
            animate="visible"
            className="mt-3"
            initial={reduceMotion ? false : "hidden"}
            key={`${execution.id}-${execution.status}`}
            variants={motionVariants.status}
          >
            <div className="flex items-start gap-3">
              <span className={`grid size-9 shrink-0 place-items-center rounded-md ${
                status === "SUCCEEDED"
                  ? "bg-positive-soft text-positive"
                  : status === "FAILED"
                    ? "bg-accent-soft text-accent"
                    : "bg-surface-raised text-ink-muted"
              }`}>
                <StatusIcon className="size-4" aria-hidden="true" />
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-bold text-ink">{labels[execution.status]}</p>
                <p className="mt-1 text-xs leading-5 text-ink-muted">{descriptions[execution.status]}</p>
                <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                  <div><dt className="text-ink-muted">执行金额</dt><dd className="mt-0.5 font-mono font-semibold text-ink">{execution.currency} {execution.amount}</dd></div>
                  <div><dt className="text-ink-muted">执行编号</dt><dd className="mt-0.5 break-all font-mono text-ink">{execution.id}</dd></div>
                  {execution.provider_reference && (
                    <div className="sm:col-span-2"><dt className="text-ink-muted">沙箱参考号</dt><dd className="mt-0.5 break-all font-mono text-ink">{execution.provider_reference}</dd></div>
                  )}
                </dl>
              </div>
            </div>
            <div className="mt-4 flex flex-wrap gap-2">
              <Button disabled={Boolean(busyAction)} onClick={() => void loadExecution()} variant="secondary">
                {busyAction === "lookup" ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <RefreshCw className="size-4" aria-hidden="true" />}
                刷新状态
              </Button>
              {(status === "RUNNING" || status === "PROCESSING") && (
                <Button disabled={Boolean(busyAction)} onClick={() => void runAction("recover")} variant="secondary">
                  {busyAction === "recover" ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <RotateCcw className="size-4" aria-hidden="true" />}
                  补查沙箱结果
                </Button>
              )}
            </div>
          </motion.div>
        )}

        {ready && !execution && !unknownOutcome && !error && (
          <motion.div
            animate="visible"
            className="mt-3"
            initial={reduceMotion ? false : "hidden"}
            key="execution-empty"
            variants={motionVariants.status}
          >
            <p className="text-xs leading-5 text-ink-muted">
              审批已批准，但尚未执行资金退款。执行将使用服务端保存的金额和固定幂等键。
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              {executeArmed ? (
                <>
                  <Button disabled={Boolean(busyAction)} onClick={() => void runAction("execute")}>
                    {busyAction === "execute" ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <Send className="size-4" aria-hidden="true" />}
                    确认执行退款
                  </Button>
                  <Button disabled={Boolean(busyAction)} onClick={() => setExecuteArmed(false)} variant="ghost">取消</Button>
                </>
              ) : (
                <Button onClick={() => setExecuteArmed(true)}>开始执行退款</Button>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {error && (
        <motion.div
          animate="visible"
          className="mt-3 flex items-start gap-2 rounded-md border border-accent/30 bg-accent-soft p-3 text-accent"
          initial={reduceMotion ? false : "hidden"}
          role="alert"
          variants={motionVariants.status}
        >
          <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <div className="min-w-0 flex-1">
            <p className="text-xs font-semibold leading-5">{error}</p>
            <Button
              className="mt-2 h-8 border-accent/30 bg-transparent px-2.5 text-xs text-accent"
              disabled={Boolean(busyAction)}
              onClick={() => void loadExecution()}
              variant="secondary"
            >
              <RefreshCw className="size-3.5" aria-hidden="true" />
              查询执行状态
            </Button>
          </div>
        </motion.div>
      )}
    </section>
  );
}
