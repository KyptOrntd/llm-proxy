import os
from dataclasses import dataclass
from typing import Literal

import yaml

# 配置文件路径
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".llm-proxy.yaml")


@dataclass
class ProviderConfig:
    base_url: str
    api_key: str | None = None
    type: Literal["DeepSeek"] | None = None
    name: str | None = None


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 9251


@dataclass
class LLMProxyConfig:
    providers: dict[str, ProviderConfig]
    models: dict[str, list[str]]
    server: ServerConfig


def parse_config() -> LLMProxyConfig:
    if not os.path.isfile(CONFIG_PATH):
        raise ValueError(f"配置文件 {CONFIG_PATH} 不存在")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    # 解析服务器配置
    server: ServerConfig = ServerConfig()
    _server = data.get("server")
    if _server is not None:
        if _server.get("host") is not None:
            server.host = _server.get("host")
        if _server.get("port") is not None:
            server.port = _server.get("port")

    # 解析模型供应商配置
    providers: dict[str, ProviderConfig] = {}
    _providers = data.get("providers")
    for provider_id in _providers:
        item = _providers[provider_id]
        providers[provider_id] = ProviderConfig(
            base_url=item["base_url"],
            api_key=item.get("api_key"),
            type=item.get("type"),
            name=item.get("name")
        )

    # 解析逻辑模型配置
    models: dict[str, list[str]] = {}
    _models = data.get("models")
    for model_id in _models:
        models[model_id] = _models[model_id]

    return LLMProxyConfig(
        providers=providers,
        models=models,
        server=server
    )


def validate_config(config: LLMProxyConfig) -> None:
    # 模型不能引用不存在的供应商 ID
    for model_id in config.models:
        for entry in config.models[model_id]:
            provider_id = entry.split("/", 1)[0]
            if provider_id in config.providers:
                continue
            raise ValueError(f"模型 {model_id} 引用了不存在的供应商 ID：{provider_id}")


CONFIG: LLMProxyConfig = parse_config()
validate_config(CONFIG)
