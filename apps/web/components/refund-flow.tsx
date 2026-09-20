"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import { Badge, Button, motionVariants } from "@ecommerce-agent-system/ui";
import {
  AlertCircle,
  Check,
  CheckCircle2,
  Clock3,
  FileCheck2,
  LoaderCircle,
  RotateCcw,
  ShieldCheck,
  X,
  XCircle,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";

import {
  assessRefund,
  confirmRefundApplication,
  createRefundApplication,
  getCurrentRefundApplication,
  RefundApiError,
  reviewRefundApplication,
} from "@/lib/refund-api";
import {
  clearRefundRequest,
  clearRefundSession,
  loadRefundRequest,
  loadRefundSession,
  saveRefundRequest,
  saveRefundSession,
  type RefundRequestDraft,
} from "@/lib/refund-session";
import { RefundExecutionPanel } from "@/components/refund-execution";
import type { OrderDetail } from "@/types/order";
import {
  REFUND_REVIEW_NOTE_MAX_LENGTH,
  type RefundApplication,
  type RefundAssessment,
  type RefundReviewDecision,
  type RefundStatus,
} from "@/types/refund";

const statusLabels: Record<RefundStatus, string> = {
  APPROVED: "审批已批准",
  AWAITING_CUSTOMER_CONFIRMATION: "等待客户确认",
  PENDING_MANUAL_APPROVAL: "等待人工审批",
  REJECTED: "审批已拒绝",
};

const reasonLabels: Record<string, string> = {
  amount_exceeds_order_total: "退款金额超过订单金额",
  currency_mismatch: "退款币种与订单不一致",
  eligible_for_review: "归属、金额、币种和订单状态校验通过",
  invalid_amount: "退款金额必须大于 0",
  order_not_owned: "订单不存在或不属于当前用户",
  order_not_refundable: "当前订单状态不支持退款",
};

type RefundAction = "assess" | "confirm" | "create" | "review";
type RefundLookupState = "error" | "idle" | "loading" | "ready";

interface RefundFlowProps {
  approvalDemoEnabled?: boolean;
  order: OrderDetail | null;
  reduceMotion: boolean;
}

function normalizeRefundError(error: unknown) {
  if (error instanceof RefundApiError) {
    return error.message;
  }
  return "网络连接失败，请检查连接后重试。";
}

function formatShanghaiDateTime(value: string | null | undefined) {
  if (!value) {
    return null;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Shanghai",
  }).format(date);
}

function createRequestId() {
  return `refund-request-${globalThis.crypto.randomUUID()}`;
}

export function RefundFlow({
  approvalDemoEnabled = false,
  order,
  reduceMotion,
}: RefundFlowProps) {
  const [application, setApplication] = useState<RefundApplication | null>(null);
  const [assessment, setAssessment] = useState<RefundAssessment | null>(null);
  const [amount, setAmount] = useState("88.00");
  const [expanded, setExpanded] = useState(false);
  const [busyAction, setBusyAction] = useState<RefundAction | null>(null);
  const [lastAction, setLastAction] = useState<RefundAction | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmArmed, setConfirmArmed] = useState(false);
  const [reviewDecision, setReviewDecision] = useState<RefundReviewDecision | null>(null);
  const [reviewNote, setReviewNote] = useState("");
  const [restored, setRestored] = useState(false);
  const [lookupState, setLookupState] = useState<RefundLookupState>(
    order ? "loading" : "idle",
  );
  const [lookupError, setLookupError] = useState<string | null>(null);
  const [lookupCurrency, setLookupCurrency] = useState<string | null>(null);
  const [lookupOrderId, setLookupOrderId] = useState<string | null>(null);
  const requestInFlight = useRef(false);
  const lookupGeneration = useRef(0);
  const requestId = useRef<string | null>(null);
  const suppliedOrderId = order?.id;

  const commitApplication = useCallback((nextApplication: RefundApplication, wasRestored = false) => {
    setApplication(nextApplication);
    setAmount(nextApplication.requested_amount);
    setRestored(wasRestored);
    setLookupState("ready");
    requestId.current = nextApplication.request_id;
    saveRefundSession(window.sessionStorage, nextApplication);
    clearRefundRequest(window.sessionStorage);
  }, []);

  const restoreRequestDraft = useCallback((draft: RefundRequestDraft | null) => {
    if (draft === null) {
      requestId.current = null;
      return;
    }
    setAmount(draft.amount);
    setExpanded(true);
    requestId.current = draft.requestId;
  }, []);

  const requestCurrentApplication = useCallback(
    async (orderId: string, fallbackCurrency: string, wasRestored: boolean) => {
      const generation = ++lookupGeneration.current;
      setLookupOrderId(orderId);
      setLookupCurrency(fallbackCurrency);
      setLookupState("loading");
      setLookupError(null);
      setApplication(null);
      setAssessment(null);
      setConfirmArmed(false);

      try {
        const currentApplication = await getCurrentRefundApplication(orderId);
        if (generation !== lookupGeneration.current) {
          return;
        }

        if (currentApplication === null) {
          clearRefundSession(window.sessionStorage);
          setApplication(null);
          setRestored(false);
          setLookupState("ready");
          return;
        }

        commitApplication(currentApplication, wasRestored);
      } catch (requestError) {
        if (generation !== lookupGeneration.current) {
          return;
        }
        setLookupError(normalizeRefundError(requestError));
        setLookupState("error");
      }
    },
    [commitApplication],
  );

  useEffect(() => {
    const savedApplication = loadRefundSession(window.sessionStorage);
    const savedRequest = loadRefundRequest(window.sessionStorage);
    const targetOrderId =
      suppliedOrderId ?? savedApplication?.order_id ?? savedRequest?.orderId;
    if (!targetOrderId) {
      return;
    }

    const fallbackCurrency =
      order?.currency ?? savedApplication?.currency ?? savedRequest?.currency ?? "CNY";
    let active = true;
    queueMicrotask(() => {
      if (!active) {
        return;
      }
      restoreRequestDraft(
        savedRequest?.orderId === targetOrderId ? savedRequest : null,
      );
      void requestCurrentApplication(
        targetOrderId,
        fallbackCurrency,
        savedApplication?.order_id === targetOrderId,
      );
    });

    return () => {
      active = false;
      lookupGeneration.current += 1;
    };
  }, [order?.currency, requestCurrentApplication, restoreRequestDraft, suppliedOrderId]);

  const currentOrderId = application?.order_id ?? order?.id ?? lookupOrderId;
  const currency = application?.currency ?? order?.currency ?? lookupCurrency ?? "CNY";
  const validAmount = /^-?\d+(?:\.\d+)?$/.test(amount);
  const canStart =
    currentOrderId !== null && application === null && lookupState === "ready";

  async function runAction(action: RefundAction) {
    if (requestInFlight.current || currentOrderId === null) {
      return;
    }
    requestInFlight.current = true;
    setBusyAction(action);
    setLastAction(action);
    setError(null);

    try {
      if (action === "assess") {
        const nextAssessment = await assessRefund(currentOrderId, amount, currency);
        setAssessment(nextAssessment);
      } else if (action === "create") {
        requestId.current ??= createRequestId();
        saveRefundRequest(window.sessionStorage, {
          amount,
          currency,
          orderId: currentOrderId,
          requestId: requestId.current,
        });
        commitApplication(
          await createRefundApplication(
            currentOrderId,
            requestId.current,
            amount,
            currency,
          ),
        );
      } else if (action === "confirm" && application) {
        commitApplication(await confirmRefundApplication(application.id));
        setConfirmArmed(false);
      } else if (action === "review" && application && reviewDecision) {
        commitApplication(
          await reviewRefundApplication(application.id, reviewDecision, reviewNote),
        );
        setReviewDecision(null);
      }
    } catch (actionError) {
      setError(normalizeRefundError(actionError));
    } finally {
      requestInFlight.current = false;
      setBusyAction(null);
    }
  }

  function handleAssessment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (validAmount) {
      void runAction("assess");
    }
  }

  function resetFlow() {
    if (requestInFlight.current) {
      return;
    }
    clearRefundSession(window.sessionStorage);
    clearRefundRequest(window.sessionStorage);
    setApplication(null);
    setAssessment(null);
    setExpanded(Boolean(currentOrderId));
    setAmount("88.00");
    setError(null);
    setReviewDecision(null);
    setRestored(false);
    requestId.current = null;
  }

  if (!order && !application && lookupState === "idle") {
    return null;
  }

  return (
    <motion.section
      animate="visible"
      aria-labelledby="refund-flow-title"
      className="mt-4 border-t border-line pt-4"
      data-motion-mode={reduceMotion ? "reduced" : "standard"}
      initial={reduceMotion ? false : "hidden"}
      variants={motionVariants.surface}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="font-mono text-[10px] uppercase text-accent">Protected action / V0.5</p>
          <h3 className="mt-1 flex items-center gap-2 text-sm font-bold text-ink" id="refund-flow-title">
            <ShieldCheck className="size-4" aria-hidden="true" />
            退款申请
          </h3>
        </div>
        {application ? (
          <Badge className="border-line-strong bg-canvas text-ink">
            {statusLabels[application.status]}
          </Badge>
        ) : lookupState === "loading" ? (
          <Badge className="border-line-strong bg-canvas text-ink-muted">
            正在读取状态
          </Badge>
        ) : lookupState === "error" ? (
          <Badge className="border-accent/30 bg-accent-soft text-accent">
            状态读取失败
          </Badge>
        ) : (
          <Button
            aria-expanded={expanded}
            disabled={!canStart}
            onClick={() => setExpanded((current) => !current)}
            variant="secondary"
          >
            <FileCheck2 className="size-4" aria-hidden="true" />
            {expanded ? "收起" : "申请退款"}
          </Button>
        )}
      </div>

      <AnimatePresence initial={false} mode="wait">
        {lookupState === "loading" && (
          <motion.p
            animate="visible"
            aria-live="polite"
            className="mt-3 flex items-center gap-2 text-xs text-ink-muted"
            exit={reduceMotion ? undefined : "hidden"}
            initial={reduceMotion ? false : "hidden"}
            key="refund-lookup-loading"
            role="status"
            variants={motionVariants.status}
          >
            <LoaderCircle className="size-3.5 animate-spin text-accent" aria-hidden="true" />
            正在读取当前退款申请
          </motion.p>
        )}
        {lookupState === "error" && lookupError && (
          <motion.div
            animate="visible"
            className="mt-3 flex items-start gap-2 rounded-md border border-accent/30 bg-accent-soft p-3 text-accent"
            exit={reduceMotion ? undefined : "hidden"}
            initial={reduceMotion ? false : "hidden"}
            key="refund-lookup-error"
            role="alert"
            variants={motionVariants.status}
          >
            <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            <div className="min-w-0 flex-1">
              <p className="text-xs font-semibold leading-5">{lookupError}</p>
              {lookupOrderId && (
                <Button
                  aria-label="重试读取退款申请"
                  className="mt-2 h-8 border-accent/30 bg-transparent px-2.5 text-xs text-accent"
                  onClick={() =>
                    void requestCurrentApplication(
                      lookupOrderId,
                      lookupCurrency ?? "CNY",
                      true,
                    )
                  }
                  variant="secondary"
                >
                  <RotateCcw className="size-3.5" aria-hidden="true" />
                  重试
                </Button>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence initial={false} mode="wait">
        {canStart && expanded && (
          <motion.div
            animate="visible"
            className="mt-4 rounded-md border border-line bg-canvas p-4"
            exit={reduceMotion ? undefined : "hidden"}
            initial={reduceMotion ? false : "hidden"}
            key="assessment"
            variants={motionVariants.status}
          >
            <form onSubmit={handleAssessment}>
              <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_7rem_auto] sm:items-end">
                <label className="text-xs font-semibold text-ink">
                  退款金额
                  <input
                    aria-label="退款金额"
                    className="mt-1.5 h-9 w-full rounded-md border border-line-strong bg-surface px-3 font-mono text-sm text-ink outline-none focus:border-ink-muted focus:outline-2 focus:outline-offset-2 focus:outline-accent"
                    disabled={Boolean(busyAction)}
                    inputMode="decimal"
                    onChange={(event) => {
                      setAmount(event.target.value);
                      setAssessment(null);
                    }}
                    value={amount}
                  />
                </label>
                <label className="text-xs font-semibold text-ink">
                  币种
                  <input
                    aria-label="退款币种"
                    className="mt-1.5 h-9 w-full rounded-md border border-line bg-surface-raised px-3 font-mono text-sm text-ink-muted"
                    readOnly
                    value={currency}
                  />
                </label>
                <Button disabled={!validAmount || Boolean(busyAction)} type="submit">
                  {busyAction === "assess" ? (
                    <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
                  ) : (
                    <ShieldCheck className="size-4" aria-hidden="true" />
                  )}
                  检查资格
                </Button>
              </div>
            </form>

            {assessment && (
              <div
                className={`mt-4 rounded-md border p-3 ${
                  assessment.eligible_for_review
                    ? "border-positive/30 bg-positive-soft text-positive"
                    : "border-accent/30 bg-accent-soft text-accent"
                }`}
                role="status"
              >
                <p className="flex items-center gap-2 text-xs font-bold">
                  {assessment.eligible_for_review ? (
                    <CheckCircle2 className="size-4" aria-hidden="true" />
                  ) : (
                    <AlertCircle className="size-4" aria-hidden="true" />
                  )}
                  {reasonLabels[assessment.reason] ?? assessment.reason}
                </p>
                {assessment.eligible_for_review && assessment.requires_customer_confirmation && (
                  <div className="mt-3 flex flex-wrap items-center justify-between gap-3 border-t border-positive/20 pt-3">
                    <p className="text-xs leading-5">
                      创建后仍需您确认，随后交由人工审批；此操作不会直接执行退款。
                    </p>
                    <Button
                      disabled={Boolean(busyAction)}
                      onClick={() => void runAction("create")}
                      variant="secondary"
                    >
                      {busyAction === "create" && (
                        <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
                      )}
                      创建申请
                    </Button>
                  </div>
                )}
              </div>
            )}
          </motion.div>
        )}

        {application && (
          <motion.article
            animate="visible"
            className="mt-4 overflow-hidden rounded-md border border-line-strong bg-canvas"
            initial={reduceMotion ? false : "hidden"}
            key={application.id}
            variants={motionVariants.status}
          >
            <div className="grid gap-px bg-line sm:grid-cols-3">
              <div className="bg-surface px-4 py-3">
                <p className="text-xs text-ink-muted">申请金额</p>
                <p className="mt-1 font-mono text-base font-bold text-ink-strong">
                  {application.currency} {application.requested_amount}
                </p>
              </div>
              <div className="bg-surface px-4 py-3">
                <p className="text-xs text-ink-muted">关联订单</p>
                <p className="mt-1 break-all font-mono text-xs font-semibold text-ink">
                  {application.order_id}
                </p>
              </div>
              <div className="bg-surface px-4 py-3">
                <p className="text-xs text-ink-muted">申请编号</p>
                <p className="mt-1 break-all font-mono text-xs font-semibold text-ink">
                  {application.id}
                </p>
              </div>
            </div>

            <div className="px-4 py-4">
              {restored && (
                <p className="mb-3 flex items-center gap-2 text-xs text-ink-muted">
                  <Clock3 className="size-3.5" aria-hidden="true" />
                  已恢复本标签页最近一次服务端状态
                </p>
              )}

              {application.status === "AWAITING_CUSTOMER_CONFIRMATION" && (
                <div>
                  <h4 className="text-sm font-bold text-ink">请确认将申请提交人工审批</h4>
                  <p className="mt-2 text-xs leading-5 text-ink-muted">
                    将为订单 {application.order_id} 申请 {application.currency} {application.requested_amount}。后端已完成归属、金额、币种和订单状态校验；确认后不能在本版撤回，但不会直接执行支付退款。
                  </p>
                  <div className="mt-4 flex flex-wrap gap-2">
                    {confirmArmed ? (
                      <>
                        <Button
                          aria-label="再次确认提交人工审批"
                          disabled={Boolean(busyAction)}
                          onClick={() => void runAction("confirm")}
                        >
                          {busyAction === "confirm" ? (
                            <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
                          ) : (
                            <Check className="size-4" aria-hidden="true" />
                          )}
                          再次确认提交
                        </Button>
                        <Button onClick={() => setConfirmArmed(false)} variant="ghost">
                          暂不确认
                        </Button>
                      </>
                    ) : (
                      <Button onClick={() => setConfirmArmed(true)}>
                        确认退款信息
                      </Button>
                    )}
                  </div>
                </div>
              )}

              {application.status === "PENDING_MANUAL_APPROVAL" && (
                <div>
                  <div className="flex items-start gap-3">
                    <span className="grid size-9 shrink-0 place-items-center rounded-md bg-accent-soft text-accent">
                      <Clock3 className="size-4" aria-hidden="true" />
                    </span>
                    <div>
                      <h4 className="text-sm font-bold text-ink">申请已暂停，等待人工决定</h4>
                      <p className="mt-1 text-xs leading-5 text-ink-muted">
                        后端状态为 PENDING_MANUAL_APPROVAL。审批结果只由服务端授权与原子状态转换决定。
                      </p>
                    </div>
                  </div>

                  <div className="mt-4 border-t border-line pt-4">
                    {!approvalDemoEnabled ? (
                      <p className="text-xs leading-5 text-ink-muted">
                        审批操作仅在受信任的审批后台开放；当前客户工作台只能查看等待状态。
                      </p>
                    ) : (
                      <>
                        <p className="font-mono text-[10px] uppercase text-ink-muted">Admin approval</p>
                        <p className="mt-1 text-xs text-ink-muted">
                          当前管理员可审批，最终权限与状态转换仍由服务端校验。
                        </p>
                    {reviewDecision === null ? (
                      <div className="mt-3 flex flex-wrap gap-2">
                        <Button onClick={() => setReviewDecision("APPROVED")} variant="secondary">
                          <CheckCircle2 className="size-4 text-positive" aria-hidden="true" />
                          准备批准
                        </Button>
                        <Button onClick={() => setReviewDecision("REJECTED")} variant="secondary">
                          <XCircle className="size-4 text-accent" aria-hidden="true" />
                          准备拒绝
                        </Button>
                      </div>
                    ) : (
                      <div className="mt-3 rounded-md border border-line bg-surface p-3">
                        <p className="text-xs font-bold text-ink">
                          将{reviewDecision === "APPROVED" ? "批准" : "拒绝"}此退款申请
                        </p>
                        <label className="mt-3 block text-xs font-semibold text-ink-muted">
                          审批备注（可选）
                          <textarea
                            aria-label="审批备注"
                            className="mt-1.5 min-h-20 w-full resize-y rounded-md border border-line-strong bg-canvas px-3 py-2 text-sm text-ink outline-none focus:border-ink-muted focus:outline-2 focus:outline-offset-2 focus:outline-accent"
                            maxLength={REFUND_REVIEW_NOTE_MAX_LENGTH}
                            onChange={(event) => setReviewNote(event.target.value)}
                            value={reviewNote}
                          />
                        </label>
                        <div className="mt-3 flex flex-wrap gap-2">
                          <Button
                            aria-label={`确认${reviewDecision === "APPROVED" ? "批准" : "拒绝"}退款申请`}
                            disabled={Boolean(busyAction)}
                            onClick={() => void runAction("review")}
                          >
                            {busyAction === "review" && (
                              <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
                            )}
                            确认{reviewDecision === "APPROVED" ? "批准" : "拒绝"}
                          </Button>
                          <Button onClick={() => setReviewDecision(null)} variant="ghost">
                            取消
                          </Button>
                        </div>
                      </div>
                    )}
                      </>
                    )}
                  </div>
                </div>
              )}

              {(application.status === "APPROVED" || application.status === "REJECTED") && (
                <div className="flex items-start gap-3">
                  <span
                    className={`grid size-9 shrink-0 place-items-center rounded-md ${
                      application.status === "APPROVED"
                        ? "bg-positive-soft text-positive"
                        : "bg-accent-soft text-accent"
                    }`}
                  >
                    {application.status === "APPROVED" ? (
                      <CheckCircle2 className="size-4" aria-hidden="true" />
                    ) : (
                      <XCircle className="size-4" aria-hidden="true" />
                    )}
                  </span>
                  <div>
                    <h4 className="text-sm font-bold text-ink">
                      {statusLabels[application.status]}
                    </h4>
                    {application.review_note && (
                      <p className="mt-1 text-xs leading-5 text-ink-muted">
                        审批备注：{application.review_note}
                      </p>
                    )}
                    {formatShanghaiDateTime(application.reviewed_at) && (
                      <p className="mt-1 font-mono text-[10px] text-ink-muted">
                        {formatShanghaiDateTime(application.reviewed_at)}
                      </p>
                    )}
                    <p className="mt-2 text-xs text-ink-muted">
                      {application.status === "APPROVED"
                        ? "审批已通过，下一步仍需单独执行退款；当前尚不能视为已退款。"
                        : "审批已拒绝，未进入资金执行。"}
                    </p>
                  </div>
                </div>
              )}

              {application.status === "APPROVED" && (
                <RefundExecutionPanel application={application} reduceMotion={reduceMotion} />
              )}
            </div>
          </motion.article>
        )}
      </AnimatePresence>

      <AnimatePresence initial={false}>
        {busyAction && (
          <motion.p
            animate="visible"
            aria-live="polite"
            className="mt-3 flex items-center gap-2 text-xs text-ink-muted"
            exit={reduceMotion ? undefined : "hidden"}
            initial={reduceMotion ? false : "hidden"}
            role="status"
            variants={motionVariants.status}
          >
            <LoaderCircle className="size-3.5 animate-spin text-accent" aria-hidden="true" />
            正在同步退款状态
          </motion.p>
        )}
        {!busyAction && error && (
          <motion.div
            animate="visible"
            className="mt-3 flex items-start gap-2 rounded-md border border-accent/30 bg-accent-soft p-3 text-accent"
            exit={reduceMotion ? undefined : "hidden"}
            initial={reduceMotion ? false : "hidden"}
            role="alert"
            variants={motionVariants.status}
          >
            <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            <div className="min-w-0 flex-1">
              <p className="text-xs font-semibold leading-5">{error}</p>
              {lastAction && (
                <Button
                  aria-label="重试退款操作"
                  className="mt-2 h-8 border-accent/30 bg-transparent px-2.5 text-xs text-accent"
                  onClick={() => void runAction(lastAction)}
                  variant="secondary"
                >
                  <RotateCcw className="size-3.5" aria-hidden="true" />
                  重试
                </Button>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {application && application.status === "REJECTED" && currentOrderId && (
        <Button className="mt-3" disabled={Boolean(busyAction)} onClick={resetFlow} variant="ghost">
          <X className="size-3.5" aria-hidden="true" />
          申请新的退款
        </Button>
      )}
    </motion.section>
  );
}
