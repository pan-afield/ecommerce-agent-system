import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings, get_settings
from app.core.database import create_database_engine
from app.services.refund import reconcile_refund_batch
from app.services.refund_recovery import process_refund_recovery_batch
from app.services.refund_sandbox import HttpRefundSandbox


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    """解析一次对账任务的参数，在创建外部资源前拒绝无效输入。"""
    parser = argparse.ArgumentParser(description="执行一次退款对账。")
    parser.add_argument(
        "--older-than-seconds",
        type=int,
        default=600,
        help="核对超过多少秒未更新的退款，默认 600 秒。",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="本次最多核对多少笔，范围 1～100。",
    )
    parser.add_argument(
        "--scheduled",
        action="store_true",
        help="处理数据库中到期的补偿任务，使用租约、退避与次数上限；只运行一批。",
    )

    arguments = parser.parse_args(argv)

    if arguments.older_than_seconds <= 0:
        parser.error("--older-than-seconds 必须大于 0")
    if not 1 <= arguments.limit <= 100:
        parser.error("--limit 必须在 1～100 之间")

    return arguments


async def run_once(
    settings: Settings,
    *,
    older_than_seconds: int,
    limit: int,
    scheduled: bool = False,
) -> dict[str, int]:
    """执行一次对账，并在成功或异常退出时释放本次创建的资源。"""
    if settings.refund_sandbox_base_url is None:
        raise ValueError("退款对账需要配置 REFUND_SANDBOX_BASE_URL")

    stale_before = datetime.now(UTC) - timedelta(seconds=older_than_seconds)
    engine = create_database_engine(settings.database_url)

    try:
        async with httpx.AsyncClient(
            base_url=str(settings.refund_sandbox_base_url),
            timeout=settings.refund_sandbox_request_timeout_seconds,
            headers=(
                {"Authorization": "Bearer " + settings.refund_sandbox_api_key.get_secret_value()}
                if settings.refund_sandbox_api_key is not None
                else {}
            ),
        ) as client:
            if scheduled:
                return await process_refund_recovery_batch(
                    engine,
                    adapter=HttpRefundSandbox(client),
                    limit=limit,
                    max_attempts=settings.refund_recovery_max_attempts,
                    lease_seconds=settings.refund_recovery_lease_seconds,
                    retry_seconds=settings.refund_recovery_retry_seconds,
                )
            return await reconcile_refund_batch(
                engine,
                stale_before=stale_before,
                adapter=HttpRefundSandbox(client),
                limit=limit,
            )
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    """执行一次命令，将对账统计转换为输出和进程退出码。"""
    arguments = parse_arguments(argv)

    try:
        counts = asyncio.run(
            run_once(
                get_settings(),
                older_than_seconds=arguments.older_than_seconds,
                limit=arguments.limit,
                scheduled=arguments.scheduled,
            )
        )
    except (ValueError, SQLAlchemyError, OSError, RuntimeError):
        # 不直接打印异常对象，避免输出连接信息或 SQL 参数。
        print("退款对账失败，请检查配置、数据库和沙箱状态。", file=sys.stderr)
        return 1

    print(f"核对完成：checked={counts['checked']}，errors={counts['errors']}")
    return 0 if counts["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
