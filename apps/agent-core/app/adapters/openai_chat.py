from collections.abc import AsyncIterator, Sequence

from langchain_core.messages import AIMessage, AnyMessage, SystemMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from openai import (
    APITimeoutError,
    AuthenticationError,
    OpenAIError,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import SecretStr

from app.services.chat import (
    ChatProviderAuthenticationError,
    ChatProviderRateLimitError,
    ChatProviderTimeoutError,
    ChatProviderUnavailableError,
)

SYSTEM_PROMPT = (
    "你是电商平台的客服助手。"
    "请用简洁、友好的中文回答一般商品、配送和售后政策问题。"
    "你不能执行退款或修改订单；订单查询只能通过系统提供的订单工具完成；"
    "并建议用户联系人工客服。"
    "不要编造订单状态、价格、库存、承诺或平台政策。"
    "没有提供订单工具时，不能编造订单信息；"
    "提供订单工具并且用户给出订单编号时，使用工具查询；"
    "工具返回错误时，向用户说明无法完成查询；"
    "仍然不能退款、修改订单或编造状态。"
)


class OpenAIChatAdapter:
    def __init__(
        self,
        *,
        api_key: SecretStr,
        model: str,
        base_url: str | None,
        reasoning_effort: str | None,
        use_responses_api: bool,
        timeout_seconds: float,
    ) -> None:
        self._client = ChatOpenAI(
            api_key=api_key,
            base_url=base_url,
            model=model,
            reasoning_effort=reasoning_effort,
            use_responses_api=use_responses_api,
            timeout=timeout_seconds,
            max_retries=0,
            store=False,
        )

    async def generate_reply(
        self,
        messages: Sequence[AnyMessage],
        tools: Sequence[BaseTool] | None = None,
    ) -> AIMessage:
        client = self._client if tools is None else self._client.bind_tools(tools)
        try:
            response = await client.ainvoke(
                [
                    SystemMessage(content=SYSTEM_PROMPT),
                    *messages,
                ]
            )
        except (AuthenticationError, PermissionDeniedError) as error:
            raise ChatProviderAuthenticationError(
                "Chat provider rejected the configured credentials."
            ) from error
        except RateLimitError as error:
            raise ChatProviderRateLimitError("Chat provider rate limit was reached.") from error
        except APITimeoutError as error:
            raise ChatProviderTimeoutError("Chat provider request timed out.") from error
        except OpenAIError as error:
            raise ChatProviderUnavailableError("Chat provider is unavailable.") from error

        return response

    async def stream_reply(
        self,
        messages: Sequence[AnyMessage],
    ) -> AsyncIterator[str]:
        try:
            async for chunk in self._client.astream(
                [
                    SystemMessage(content=SYSTEM_PROMPT),
                    *messages,
                ]
            ):
                text = chunk.text
                if text:
                    yield text
        except (AuthenticationError, PermissionDeniedError) as error:
            raise ChatProviderAuthenticationError(
                "Chat provider rejected the configured credentials."
            ) from error
        except RateLimitError as error:
            raise ChatProviderRateLimitError("Chat provider rate limit was reached.") from error
        except APITimeoutError as error:
            raise ChatProviderTimeoutError("Chat provider request timed out.") from error
        except OpenAIError as error:
            raise ChatProviderUnavailableError("Chat provider is unavailable.") from error
