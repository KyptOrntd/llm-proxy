import os
from typing import Literal

import httpx
import yaml

# 生成过程中两个 token 之间可能停顿很久，读超时必须设成永不超时。
TIMEOUT = httpx.Timeout(connect=10.0, read=None, write=60.0, pool=10.0)

# 用户配置文件：供应商信息存在 providers 段里，key 是 provider_id。
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".llm-proxy.yaml")


def load_providers(path: str = CONFIG_PATH) -> dict:
    """从用户配置文件里读取 providers 段，key 是 provider_id；文件或段缺失就抛 ValueError。"""
    if not os.path.isfile(path):
        raise ValueError("找不到用户配置文件：" + path)
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    providers = None
    if isinstance(data, dict):
        providers = data.get("providers")
    if not isinstance(providers, dict):
        raise ValueError("用户配置文件里没有 providers 段：" + path)
    return providers


class ModelCaller:
    """模型调用器：持有共用的 httpx.AsyncClient，按 provider_id 发起调用。

    供应商配置从用户配置文件 ~/.llm-proxy.yaml 的 providers 段读取，形状：
    {provider_id: {"base_url": ..., "api_key": ..., "type": ...}}。
    整个调用器共用一个客户端（连接池复用），不用了调 aclose()，
    或者用 async with 包起来让它自己关。
    """

    def __init__(self, timeout=TIMEOUT) -> None:
        self.providers = load_providers()
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
        # 解析供应商
        provider = self.providers.get(provider_id)

        # 构建请求地址
        url = provider["base_url"].rstrip("/") + "/chat/completions"

        # 构建请求头
        headers = {}
        api_key = provider.get("api_key")
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
        provider_type = provider.get("type")
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

    async def aclose(self) -> None:
        """关闭内部客户端，释放连接池。"""
        await self._client.aclose()

    async def __aenter__(self) -> "ModelCaller":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()
