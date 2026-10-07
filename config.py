"""配置解析：读用户配置文件 ~/.llm-proxy.yaml，对外提供全局配置对象 CONFIG。

providers 段的 key 是 provider_id；models 段的 key 是逻辑模型 id，
值是「供应商id/模型id」格式的列表。文件或 providers 段缺失时抛 ValueError。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

import yaml

# 用户配置文件路径，固定在用户主目录下。
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".llm-proxy.yaml")


@dataclass
class ProviderConfig:
    base_url: str
    api_key: str | None = None
    type: Literal["DeepSeek"] | None = None
    name: str | None = None


@dataclass
class Config:
    providers: dict[str, ProviderConfig]
    models: dict[str, list[str]]


def load_config(path: str = CONFIG_PATH) -> Config:
    if not os.path.isfile(path):
        raise ValueError("找不到用户配置文件：" + path)
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    raw_providers = None
    if isinstance(data, dict):
        raw_providers = data.get("providers")
    if not isinstance(raw_providers, dict):
        raise ValueError("用户配置文件里没有 providers 段：" + path)
    providers = {}
    for provider_id in raw_providers:
        item = raw_providers[provider_id]
        providers[provider_id] = ProviderConfig(
            base_url=item["base_url"],
            api_key=item.get("api_key"),
            type=item.get("type"),
        )
    # models 段可选：不配就是空表；配了就必须是「逻辑模型id -> 列表」。
    raw_models = None
    if isinstance(data, dict):
        raw_models = data.get("models")
    models = {}
    if raw_models is not None:
        if not isinstance(raw_models, dict):
            raise ValueError("用户配置文件里的 models 段必须是「逻辑模型id -> 列表」的映射：" + path)
        for model_id in raw_models:
            targets = raw_models[model_id]
            if not isinstance(targets, list) or not targets:
                raise ValueError("逻辑模型 " + repr(model_id) + " 的值必须是非空列表：" + path)
            for target in targets:
                if not isinstance(target, str) or "/" not in target:
                    raise ValueError(
                        "逻辑模型 " + repr(model_id) + " 的条目必须是「供应商id/模型id」格式：" + str(target)
                    )
            models[model_id] = targets
    return Config(providers=providers, models=models)


# 全局配置对象：模块导入时读一次配置文件。
CONFIG: Config = load_config()
