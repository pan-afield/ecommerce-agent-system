import asyncio
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from sqlalchemy.exc import SQLAlchemyError

import app.refund_reconciliation_cli as reconciliation_cli
from app.core.config import Settings
from app.refund_reconciliation_cli import parse_arguments
from app.services.refund_sandbox import HttpRefundSandbox


def test_parse_arguments_uses_defaults() -> None:
    arguments = parse_arguments([])

    assert arguments.older_than_seconds == 600
    assert arguments.limit == 50


@pytest.mark.parametrize("limit", [1, 3, 100])
def test_parse_arguments_accepts_overrides_and_boundaries(limit: int) -> None:
    arguments = parse_arguments(["--older-than-seconds", "1", "--limit", str(limit)])

    assert arguments.older_than_seconds == 1
    assert arguments.limit == limit


@pytest.mark.parametrize(
    ("argv", "error_message"),
    [
        (["--older-than-seconds", "0"], "--older-than-seconds 必须大于 0"),
        (["--older-than-seconds", "-1"], "--older-than-seconds 必须大于 0"),
        (["--limit", "0"], "--limit 必须在 1～100 之间"),
        (["--limit", "101"], "--limit 必须在 1～100 之间"),
        (["--limit", "1.5"], "--limit"),
        (["--older-than-seconds", "tomorrow"], "--older-than-seconds"),
        (["--limit"], "--limit"),
        (["--unknown"], "--unknown"),
    ],
)
def test_parse_arguments_rejects_invalid_input(
    argv: list[str],
    error_message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as error:
        parse_arguments(argv)

    assert error.value.code == 2
    output = capsys.readouterr()
    assert error_message in output.err
    assert output.out == ""


def test_parse_arguments_help_exits_successfully(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        parse_arguments(["--help"])

    assert error.value.code == 0
    output = capsys.readouterr()
    assert "--older-than-seconds" in output.out
    assert "--limit" in output.out
    assert output.err == ""


def test_parse_arguments_reads_process_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["refund_reconciliation_cli", "--limit", "3"])

    arguments = parse_arguments()

    assert arguments.older_than_seconds == 600
    assert arguments.limit == 3


@dataclass
class RunnerResources:
    settings: Settings
    engine: Mock
    create_engine: Mock
    create_client: Mock
    batch: AsyncMock
    requests: list[httpx.Request]
    clients: list[httpx.AsyncClient]


@pytest.fixture
def resources(monkeypatch: pytest.MonkeyPatch) -> RunnerResources:
    """替换数据库和 HTTP 传输，保留真实客户端的上下文关闭行为。"""
    settings = Settings(
        _env_file=None,
        database_url="postgresql://test:test@localhost/ecommerce_agents_test",
        refund_sandbox_base_url="https://sandbox.test",
        refund_sandbox_request_timeout_seconds=7.5,
    )
    engine = Mock(dispose=AsyncMock())
    create_engine = Mock(return_value=engine)
    requests: list[httpx.Request] = []
    clients: list[httpx.AsyncClient] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET", "对账不得重新执行退款"
        return httpx.Response(404)

    def create_http_client(
        *, base_url: str, timeout: float, headers: dict[str, str]
    ) -> httpx.AsyncClient:
        client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers=headers,
            transport=httpx.MockTransport(respond),
        )
        clients.append(client)
        return client

    create_client = Mock(side_effect=create_http_client)
    batch = AsyncMock(return_value={"checked": 2, "errors": 1})
    monkeypatch.setattr(reconciliation_cli, "create_database_engine", create_engine)
    # 仅替换被测模块的引用，不修改其他测试使用的 httpx 模块。
    monkeypatch.setattr(reconciliation_cli, "httpx", SimpleNamespace(AsyncClient=create_client))
    monkeypatch.setattr(reconciliation_cli, "reconcile_refund_batch", batch)
    return RunnerResources(settings, engine, create_engine, create_client, batch, requests, clients)


async def test_run_once_passes_configuration_and_returns_batch_counts(
    resources: RunnerResources,
) -> None:
    before = datetime.now(UTC)

    async def run_batch(
        engine: object,
        *,
        stale_before: datetime,
        adapter: HttpRefundSandbox,
        limit: int,
    ) -> dict[str, int]:
        assert engine is resources.engine
        assert before - timedelta(seconds=600) <= stale_before
        assert stale_before <= datetime.now(UTC) - timedelta(seconds=600)
        assert stale_before.tzinfo is UTC
        assert limit == 3
        assert isinstance(adapter, HttpRefundSandbox)
        assert not resources.clients[0].is_closed
        resources.engine.dispose.assert_not_awaited()
        assert await adapter.get_result("refund-key") is None
        return {"checked": 2, "errors": 1}

    resources.batch.side_effect = run_batch

    counts = await reconciliation_cli.run_once(resources.settings, older_than_seconds=600, limit=3)

    assert counts == {"checked": 2, "errors": 1}
    resources.batch.assert_awaited_once()
    resources.create_engine.assert_called_once_with(resources.settings.database_url)
    resources.create_client.assert_called_once_with(
        base_url="https://sandbox.test/", timeout=7.5, headers={}
    )
    assert len(resources.requests) == 1
    assert str(resources.requests[0].url) == "https://sandbox.test/refunds/refund-key"
    assert resources.requests[0].extensions["timeout"]["read"] == 7.5
    assert resources.clients[0].is_closed
    resources.engine.dispose.assert_awaited_once_with()


async def test_scheduled_run_uses_persistent_jobs_and_authenticated_client(
    resources: RunnerResources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pydantic import SecretStr

    resources.settings.refund_sandbox_api_key = SecretStr("fixture-sandbox-token")
    scheduled = AsyncMock(return_value={"checked": 1, "errors": 0})
    monkeypatch.setattr(reconciliation_cli, "process_refund_recovery_batch", scheduled)
    assert await reconciliation_cli.run_once(
        resources.settings,
        older_than_seconds=600,
        limit=3,
        scheduled=True,
    ) == {"checked": 1, "errors": 0}
    resources.batch.assert_not_awaited()
    scheduled.assert_awaited_once()
    assert scheduled.await_args is not None
    assert scheduled.await_args.kwargs["max_attempts"] == 5
    assert scheduled.await_args.kwargs["lease_seconds"] == 120
    assert scheduled.await_args.kwargs["retry_seconds"] == 60
    assert resources.clients[0].headers["Authorization"] == "Bearer fixture-sandbox-token"
    assert resources.clients[0].is_closed
    resources.engine.dispose.assert_awaited_once_with()
    assert reconciliation_cli.parse_arguments(["--scheduled"]).scheduled is True


async def test_run_once_rejects_missing_sandbox_before_creating_resources(
    resources: RunnerResources,
) -> None:
    settings = resources.settings.model_copy(update={"refund_sandbox_base_url": None})

    with pytest.raises(ValueError, match="REFUND_SANDBOX_BASE_URL"):
        await reconciliation_cli.run_once(settings, older_than_seconds=600, limit=3)

    resources.create_engine.assert_not_called()
    resources.create_client.assert_not_called()
    resources.batch.assert_not_awaited()


@pytest.mark.parametrize(
    "failure",
    [SQLAlchemyError("scan failed"), TimeoutError("timeout"), RuntimeError("invalid response")],
)
async def test_run_once_propagates_failure_after_releasing_resources(
    resources: RunnerResources,
    failure: Exception,
) -> None:
    resources.batch.side_effect = failure

    with pytest.raises(type(failure)) as error:
        await reconciliation_cli.run_once(resources.settings, older_than_seconds=600, limit=3)

    assert error.value is failure
    assert resources.clients[0].is_closed
    resources.engine.dispose.assert_awaited_once_with()


async def test_run_once_cancellation_releases_resources(resources: RunnerResources) -> None:
    started = asyncio.Event()
    pending = asyncio.Event()

    async def wait_for_provider(*args: object, **kwargs: object) -> dict[str, int]:
        started.set()
        await pending.wait()
        raise AssertionError("已取消的任务不应继续执行")

    resources.batch.side_effect = wait_for_provider
    task = asyncio.create_task(
        reconciliation_cli.run_once(resources.settings, older_than_seconds=600, limit=3)
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert resources.clients[0].is_closed
        resources.engine.dispose.assert_awaited_once_with()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_run_once_client_creation_failure_disposes_engine(
    resources: RunnerResources,
) -> None:
    resources.create_client.side_effect = RuntimeError("client creation failed")

    with pytest.raises(RuntimeError, match="client creation failed"):
        await reconciliation_cli.run_once(resources.settings, older_than_seconds=600, limit=3)

    resources.batch.assert_not_awaited()
    resources.engine.dispose.assert_awaited_once_with()


async def test_run_once_client_close_failure_still_disposes_engine(
    resources: RunnerResources,
) -> None:
    class FailingCloseTransport(httpx.AsyncBaseTransport):
        async def aclose(self) -> None:
            raise RuntimeError("client close failed")

    client = httpx.AsyncClient(transport=FailingCloseTransport())
    resources.create_client.side_effect = None
    resources.create_client.return_value = client

    with pytest.raises(RuntimeError, match="client close failed"):
        await reconciliation_cli.run_once(resources.settings, older_than_seconds=600, limit=3)

    resources.batch.assert_awaited_once()
    assert client.is_closed
    resources.engine.dispose.assert_awaited_once_with()


async def test_run_once_engine_disposal_failure_does_not_report_success(
    resources: RunnerResources,
) -> None:
    resources.engine.dispose.side_effect = RuntimeError("engine disposal failed")

    with pytest.raises(RuntimeError, match="engine disposal failed"):
        await reconciliation_cli.run_once(resources.settings, older_than_seconds=600, limit=3)

    assert resources.clients[0].is_closed
    resources.engine.dispose.assert_awaited_once_with()


@pytest.mark.parametrize(
    ("argv", "counts", "expected_age", "expected_limit", "expected_exit_code"),
    [
        ([], {"checked": 0, "errors": 0}, 600, 50, 0),
        (["--older-than-seconds", "60", "--limit", "3"], {"checked": 3, "errors": 0}, 60, 3, 0),
        ([], {"checked": 2, "errors": 1}, 600, 50, 1),
        ([], {"checked": 0, "errors": 3}, 600, 50, 1),
    ],
)
def test_main_awaits_run_once_reports_counts_and_returns_exit_code(
    resources: RunnerResources,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    counts: dict[str, int],
    expected_age: int,
    expected_limit: int,
    expected_exit_code: int,
) -> None:
    get_settings = Mock(return_value=resources.settings)
    run_once = AsyncMock(return_value=counts)
    monkeypatch.setattr(reconciliation_cli, "get_settings", get_settings)
    monkeypatch.setattr(reconciliation_cli, "run_once", run_once)

    exit_code = reconciliation_cli.main(argv)

    assert exit_code == expected_exit_code
    get_settings.assert_called_once_with()
    run_once.assert_awaited_once_with(
        resources.settings, older_than_seconds=expected_age, limit=expected_limit, scheduled=False
    )
    output = capsys.readouterr()
    assert output.out == f"核对完成：checked={counts['checked']}，errors={counts['errors']}\n"
    assert output.err == ""
    resources.create_engine.assert_not_called()
    resources.create_client.assert_not_called()


@pytest.mark.parametrize("error_type", [ValueError, SQLAlchemyError, TimeoutError, RuntimeError])
def test_main_sanitizes_expected_errors_without_success_output(
    resources: RunnerResources,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error_type: type[Exception],
) -> None:
    run_once = AsyncMock(side_effect=error_type("fixture-secret: SELECT private_record"))
    monkeypatch.setattr(reconciliation_cli, "get_settings", Mock(return_value=resources.settings))
    monkeypatch.setattr(reconciliation_cli, "run_once", run_once)

    assert reconciliation_cli.main([]) == 1

    run_once.assert_awaited_once()
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "退款对账失败，请检查配置、数据库和沙箱状态。\n"


def test_main_configuration_failure_does_not_start_coroutine(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    get_settings = Mock(side_effect=ValueError("fixture-secret: invalid configuration"))
    run_once = AsyncMock()
    monkeypatch.setattr(reconciliation_cli, "get_settings", get_settings)
    monkeypatch.setattr(reconciliation_cli, "run_once", run_once)

    assert reconciliation_cli.main([]) == 1

    get_settings.assert_called_once_with()
    run_once.assert_not_called()
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "退款对账失败，请检查配置、数据库和沙箱状态。\n"


@pytest.mark.parametrize(
    ("argv", "expected_exit_code"),
    [(["--help"], 0), (["--limit", "101"], 2)],
)
def test_main_argument_exit_happens_before_loading_configuration(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    expected_exit_code: int,
) -> None:
    get_settings = Mock()
    run_once = AsyncMock()
    monkeypatch.setattr(reconciliation_cli, "get_settings", get_settings)
    monkeypatch.setattr(reconciliation_cli, "run_once", run_once)

    with pytest.raises(SystemExit) as error:
        reconciliation_cli.main(argv)

    assert error.value.code == expected_exit_code
    get_settings.assert_not_called()
    run_once.assert_not_called()


def test_main_waits_for_resource_cleanup_before_reporting_success(
    resources: RunnerResources,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def dispose() -> None:
        assert resources.clients[0].is_closed
        output = capsys.readouterr()
        assert output.out == ""
        assert output.err == ""

    resources.engine.dispose.side_effect = dispose
    resources.batch.return_value = {"checked": 1, "errors": 0}
    monkeypatch.setattr(reconciliation_cli, "get_settings", Mock(return_value=resources.settings))

    assert reconciliation_cli.main([]) == 0

    resources.batch.assert_awaited_once()
    resources.engine.dispose.assert_awaited_once_with()
    assert capsys.readouterr().out == "核对完成：checked=1，errors=0\n"


def test_main_missing_sandbox_configuration_reports_failure_without_resources(
    resources: RunnerResources,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = resources.settings.model_copy(update={"refund_sandbox_base_url": None})
    monkeypatch.setattr(reconciliation_cli, "get_settings", Mock(return_value=settings))

    assert reconciliation_cli.main([]) == 1

    resources.create_engine.assert_not_called()
    resources.create_client.assert_not_called()
    resources.batch.assert_not_awaited()
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "退款对账失败，请检查配置、数据库和沙箱状态。\n"


def test_main_connection_refused_is_sanitized_after_cleanup(
    resources: RunnerResources,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """数据库未启动时可能直接抛 OSError 子类，不一定包装成 SQLAlchemyError。"""
    resources.batch.side_effect = ConnectionRefusedError("fixture-private-database:5432")
    monkeypatch.setattr(reconciliation_cli, "get_settings", Mock(return_value=resources.settings))

    assert reconciliation_cli.main([]) == 1

    assert resources.clients[0].is_closed
    resources.engine.dispose.assert_awaited_once_with()
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "退款对账失败，请检查配置、数据库和沙箱状态。\n"
