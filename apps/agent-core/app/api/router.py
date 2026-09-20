from fastapi import APIRouter

from app.api.routes.auth import router as auth_router
from app.api.routes.chat import router as chat_router
from app.api.routes.health import router as health_router
from app.api.routes.orders import router as orders_router
from app.api.routes.rag import router as rag_router
from app.api.routes.refund_operations import router as refund_operations_router
from app.api.routes.refund_webhooks import router as refund_webhook_router
from app.api.routes.refunds import router as refunds_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(chat_router)
api_router.include_router(orders_router)
api_router.include_router(refunds_router)
api_router.include_router(rag_router)
api_router.include_router(auth_router)
api_router.include_router(refund_webhook_router)
api_router.include_router(refund_operations_router)
