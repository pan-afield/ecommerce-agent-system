import { proxyRefundOperationAction } from "@/lib/refund-operation-route";

interface RouteContext { params: Promise<{ applicationId: string }> }

export function POST(request: Request, context: RouteContext) {
  return proxyRefundOperationAction(request, context, "resume");
}
