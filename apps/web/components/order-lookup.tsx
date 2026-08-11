"use client";

import { useRef, useState, type FormEvent } from "react";

import {
  Badge,
  Button,
  motionTransitions,
  motionVariants,
} from "@ecommerce-agent-system/ui";
import {
  AlertCircle,
  CalendarDays,
  LoaderCircle,
  MapPin,
  PackageCheck,
  RotateCcw,
  Search,
  Truck,
} from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";

import { getOrder, OrderApiError } from "@/lib/order-api";
import {
  ORDER_ID_MAX_LENGTH,
  type OrderDetail,
  type OrderErrorDetail,
} from "@/types/order";

const DEMO_ORDER_ID = "order-demo-001";

const orderStatusLabels: Record<string, string> = {
  cancelled: "已取消",
  delivered: "已送达",
  paid: "已支付",
  pending: "待处理",
  processing: "处理中",
  shipped: "已发货",
};

const shipmentStatusLabels: Record<string, string> = {
  confirmed: "已确认",
  delivered: "已送达",
  exception: "运输异常",
  in_transit: "运输中",
  packed: "已打包",
  shipped: "已发货",
};

const shanghaiDateTime = new Intl.DateTimeFormat("zh-CN", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "Asia/Shanghai",
});

function formatDateTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : shanghaiDateTime.format(date);
}

function readableStatus(status: string, labels: Record<string, string>) {
  return labels[status] ?? `未知状态（${status}）`;
}

function normalizeOrderError(error: unknown): OrderErrorDetail {
  if (error instanceof OrderApiError) {
    return { code: error.code, message: error.message };
  }

  return {
    code: "order_network_error",
    message: "网络连接失败，请检查连接后重试。",
  };
}

interface OrderDetailCardProps {
  order: OrderDetail;
  reduceMotion: boolean;
}

function OrderDetailCard({ order, reduceMotion }: OrderDetailCardProps) {
  return (
    <motion.article
      animate="visible"
      aria-labelledby="order-detail-title"
      className="mt-4 overflow-hidden rounded-md border border-line-strong bg-surface shadow-[0_1px_2px_rgba(23,33,29,0.04)]"
      data-motion-mode={reduceMotion ? "reduced" : "standard"}
      initial={reduceMotion ? false : "hidden"}
      variants={motionVariants.surface}
    >
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-4 py-3.5">
        <div className="min-w-0">
          <p className="font-mono text-[10px] uppercase text-ink-muted">Order detail</p>
          <h3 className="mt-1 break-all text-sm font-bold text-ink-strong" id="order-detail-title">
            {order.order_number}
          </h3>
          <p className="mt-1 break-all font-mono text-[10px] text-ink-muted">{order.id}</p>
        </div>
        <Badge className="border-positive/30 bg-positive-soft text-positive">
          <span className="mr-1.5 size-1.5 rounded-full bg-positive" aria-hidden="true" />
          {readableStatus(order.status, orderStatusLabels)}
        </Badge>
      </div>

      <dl className="grid grid-cols-1 divide-y divide-line border-b border-line sm:grid-cols-2 sm:divide-x sm:divide-y-0">
        <div className="px-4 py-3">
          <dt className="text-xs text-ink-muted">订单金额</dt>
          <dd className="mt-1 font-mono text-base font-semibold text-ink-strong">
            {order.currency} {order.total_amount}
          </dd>
        </div>
        <div className="px-4 py-3">
          <dt className="flex items-center gap-1.5 text-xs text-ink-muted">
            <CalendarDays className="size-3.5" aria-hidden="true" />
            创建时间
          </dt>
          <dd className="mt-1 text-sm font-semibold text-ink">
            {formatDateTime(order.created_at)}
          </dd>
        </div>
      </dl>

      <div className="px-4 py-4">
        <div className="mb-4 flex items-center justify-between">
          <h4 className="flex items-center gap-2 text-sm font-bold text-ink">
            <Truck className="size-4 text-accent" aria-hidden="true" />
            物流进度
          </h4>
          <span className="font-mono text-[10px] text-ink-muted">
            {order.shipment_events.length} EVENTS
          </span>
        </div>

        {order.shipment_events.length === 0 ? (
          <p className="rounded-md bg-canvas px-3 py-3 text-sm text-ink-muted">暂无物流节点</p>
        ) : (
          <ol className="space-y-0" aria-label="物流节点">
            {order.shipment_events.map((event, index) => (
              <motion.li
                animate="visible"
                className="relative grid grid-cols-[20px_minmax(0,1fr)] gap-3 pb-4 last:pb-0"
                initial={reduceMotion ? false : "hidden"}
                key={event.id}
                transition={
                  reduceMotion
                    ? { duration: 0 }
                    : { ...motionTransitions.feedback, delay: Math.min(index * 0.025, 0.1) }
                }
                variants={motionVariants.append}
              >
                {index < order.shipment_events.length - 1 && (
                  <span
                    className="absolute left-[9px] top-4 h-[calc(100%-8px)] w-px bg-line-strong"
                    aria-hidden="true"
                  />
                )}
                <span className="relative z-10 mt-0.5 grid size-5 place-items-center rounded-full border-2 border-surface bg-positive text-white">
                  <PackageCheck className="size-3" aria-hidden="true" />
                </span>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                    <p className="text-sm font-semibold text-ink">{event.description}</p>
                    <time className="font-mono text-[10px] text-ink-muted" dateTime={event.occurred_at}>
                      {formatDateTime(event.occurred_at)}
                    </time>
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-muted">
                    <span>{readableStatus(event.status, shipmentStatusLabels)}</span>
                    {event.location && (
                      <span className="inline-flex items-center gap-1">
                        <MapPin className="size-3" aria-hidden="true" />
                        {event.location}
                      </span>
                    )}
                  </div>
                </div>
              </motion.li>
            ))}
          </ol>
        )}
      </div>
    </motion.article>
  );
}

export function OrderLookup() {
  const [orderId, setOrderId] = useState(DEMO_ORDER_ID);
  const [activeOrderId, setActiveOrderId] = useState<string | null>(null);
  const [lastSubmittedOrderId, setLastSubmittedOrderId] = useState(DEMO_ORDER_ID);
  const [order, setOrder] = useState<OrderDetail | null>(null);
  const [error, setError] = useState<OrderErrorDetail | null>(null);
  const requestInFlight = useRef(false);
  const shouldReduceMotion = useReducedMotion() ?? false;
  const normalizedOrderId = orderId.trim();
  const canSubmit = normalizedOrderId.length > 0 && !activeOrderId;

  async function requestOrder(requestedOrderId: string) {
    const normalizedId = requestedOrderId.trim();
    if (!normalizedId || requestInFlight.current) {
      return;
    }

    requestInFlight.current = true;
    setLastSubmittedOrderId(normalizedId);
    setActiveOrderId(normalizedId);
    setOrder(null);
    setError(null);

    try {
      setOrder(await getOrder(normalizedId));
    } catch (requestError) {
      setError(normalizeOrderError(requestError));
    } finally {
      requestInFlight.current = false;
      setActiveOrderId(null);
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (canSubmit) {
      void requestOrder(normalizedOrderId);
    }
  }

  return (
    <section className="shrink-0 border-b border-line bg-surface-raised" aria-labelledby="order-lookup-title">
      <div className="mx-auto max-h-[44dvh] max-w-4xl overflow-y-auto px-5 py-4 sm:px-8">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="font-mono text-[10px] uppercase text-accent">Direct action / V0.2</p>
            <h2 className="mt-1 flex items-center gap-2 text-sm font-bold text-ink" id="order-lookup-title">
              <PackageCheck className="size-4" aria-hidden="true" />
              订单查询
            </h2>
          </div>

          <form className="flex w-full gap-2 sm:max-w-md" onSubmit={handleSubmit}>
            <div className="min-w-0 flex-1">
              <label className="sr-only" htmlFor="order-id">
                订单 ID
              </label>
              <input
                className="h-9 w-full rounded-md border border-line-strong bg-surface px-3 font-mono text-xs text-ink outline-none transition-colors placeholder:text-ink-muted/70 focus:border-ink-muted focus:outline-2 focus:outline-offset-2 focus:outline-accent disabled:cursor-wait disabled:opacity-60"
                disabled={Boolean(activeOrderId)}
                id="order-id"
                maxLength={ORDER_ID_MAX_LENGTH}
                onChange={(event) => setOrderId(event.target.value)}
                placeholder="订单 ID"
                value={orderId}
              />
            </div>
            <Button aria-label="查询订单" disabled={!canSubmit} type="submit">
              {activeOrderId ? (
                <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
              ) : (
                <Search className="size-4" aria-hidden="true" />
              )}
              查询
            </Button>
          </form>
        </div>

        <AnimatePresence initial={false} mode="wait">
          {activeOrderId && (
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
              正在查询订单 {activeOrderId}
            </motion.div>
          )}

          {!activeOrderId && error && (
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
                <p className="text-xs font-semibold leading-5">{error.message}</p>
                <Button
                  aria-label="重试订单查询"
                  className="mt-2 h-8 border-accent/30 bg-transparent px-2.5 text-xs text-accent hover:border-accent hover:bg-surface"
                  onClick={() => void requestOrder(lastSubmittedOrderId)}
                  variant="secondary"
                >
                  <RotateCcw className="size-3.5" aria-hidden="true" />
                  重试
                </Button>
              </div>
            </motion.div>
          )}

          {!activeOrderId && order && (
            <OrderDetailCard
              key={order.id}
              order={order}
              reduceMotion={shouldReduceMotion}
            />
          )}
        </AnimatePresence>
      </div>
    </section>
  );
}
