"use client";

import { useRef, useState, type FormEvent } from "react";

import {
  Button,
  motionVariants,
} from "@ecommerce-agent-system/ui";
import {
  AlertCircle,
  LoaderCircle,
  PackageCheck,
  RotateCcw,
  Search,
} from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";

import { OrderDetailCard } from "@/components/order-detail-card";
import { RefundFlow } from "@/components/refund-flow";
import { getOrder, OrderApiError } from "@/lib/order-api";
import {
  ORDER_ID_MAX_LENGTH,
  type OrderDetail,
  type OrderErrorDetail,
} from "@/types/order";

const DEMO_ORDER_ID = "order-demo-001";

function normalizeOrderError(error: unknown): OrderErrorDetail {
  if (error instanceof OrderApiError) {
    return { code: error.code, message: error.message };
  }

  return {
    code: "order_network_error",
    message: "网络连接失败，请检查连接后重试。",
  };
}

interface OrderLookupProps {
  approvalDemoEnabled?: boolean;
}

export function OrderLookup({ approvalDemoEnabled = false }: OrderLookupProps) {
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
    <section className="min-w-0" aria-labelledby="order-lookup-title">
      <div className="p-4">
        <div>
          <p className="font-mono text-[10px] uppercase text-accent">Direct action / V0.2</p>
          <h2 className="mt-1 flex items-center gap-2 text-sm font-bold text-ink" id="order-lookup-title">
            <PackageCheck className="size-4" aria-hidden="true" />
            订单查询
          </h2>
          <form className="mt-3 flex w-full gap-2" onSubmit={handleSubmit}>
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
            <div key={order.id}>
              <OrderDetailCard order={order} reduceMotion={shouldReduceMotion} />
              <RefundFlow
                approvalDemoEnabled={approvalDemoEnabled}
                order={order}
                reduceMotion={shouldReduceMotion}
              />
            </div>
          )}
        </AnimatePresence>
        {!activeOrderId && !order && (
          <RefundFlow
            approvalDemoEnabled={approvalDemoEnabled}
            order={null}
            reduceMotion={shouldReduceMotion}
          />
        )}
      </div>
    </section>
  );
}
