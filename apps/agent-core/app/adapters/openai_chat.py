from langchain_core.messages import HumanMessage, SystemMessage
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
    "你无法查询真实订单或执行退款；缺少信息时请明确说明，"
    "并建议用户联系人工客服。"
    "不要编造订单状态、价格、库存、承诺或平台政策。"
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

    async def generate_reply(self, message: str) -> str:
        try:
            response = await self._client.ainvoke(
                [
                    SystemMessage(content=SYSTEM_PROMPT),
                    HumanMessage(content=message),
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

        return response.text
