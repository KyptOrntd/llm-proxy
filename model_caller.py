import random
from typing import Literal

import httpx

from config import CONFIG

# 生成过程中两个 token 之间可能停顿很久，读超时必须设成永不超时。
TIMEOUT = httpx.Timeout(connect=10.0, read=None, write=60.0, pool=10.0)


class ModelCaller:
    """模型调用器：持有共用的 httpx.AsyncClient，按 provider_id 发起调用。

    供应商配置来自全局配置对象 CONFIG（见 config.py，读 ~/.llm-proxy.yaml 的
    providers 段），CONFIG.providers 按 provider_id 索引，值是 ProviderConfig（字段用点号取）。
    整个调用器共用一个客户端（连接池复用），不用了调 aclose()，
    或者用 async with 包起来让它自己关。
    """

    def __init__(self, timeout=TIMEOUT) -> None:
        self._client = httpx.AsyncClient(timeout=timeout)

    async def call(
            self,
            provider_id: str,
            model_id: str,
            messages: list,
            reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] = "none",
            stream: bool = True,
            **extras
    ) -> httpx.Response:
        provider = CONFIG.providers[provider_id]

        # 构建请求地址
        url = provider.base_url.rstrip("/") + "/chat/completions"

        # 构建请求头
        headers = {}
        api_key = provider.api_key
        if api_key:
            headers["authorization"] = "Bearer " + api_key

        # 构建请求体
        body = {
            "model": model_id,
            "messages": messages,
            "reasoning_effort": reasoning_effort,
            "stream": stream
        }
        for key in extras:
            body[key] = extras[key]

        # 根据不同供应商重写请求体
        provider_type = provider.type
        if provider_type == "DeepSeek":
            if reasoning_effort == "none":
                body["thinking"] = {"type": "disabled"}
                del body["reasoning_effort"]
            else:
                body["thinking"] = {"type": "enabled"}

        # 处理流式响应
        if stream:
            request = self._client.build_request(
                "POST",
                url,
                headers=headers,
                json=body,
            )
            return await self._client.send(request, stream=True)

        # 处理非流式响应
        return await self._client.post(
            url,
            headers=headers,
            json=body,
        )

    async def call_logical(
            self,
            model_id: str,
            messages: list,
            reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] = "none",
            stream: bool = True,
            **extras
    ) -> httpx.Response:
        """按逻辑模型调用：从 CONFIG.models 里随机挑一个条目，再交给 call() 发出去。

        条目是「供应商id/模型id」，按第一个 / 拆开 —— 模型 id 里再带 / 也没问题。
        每次调用都重新随机挑一次。
        """
        entry = random.choice(CONFIG.models[model_id])
        provider_id, target_model_id = entry.split("/", 1)
        return await self.call(
            provider_id,
            target_model_id,
            messages,
            reasoning_effort=reasoning_effort,
            stream=stream,
            **extras
        )

    async def aclose(self) -> None:
        """关闭内部客户端，释放连接池。"""
        await self._client.aclose()

    async def __aenter__(self) -> "ModelCaller":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()
