# AGENTS.md

给在这个仓库里干活的编码助手看的上下文。面向使用者的说明在 `README.md`。

## 项目是什么

极简的 OpenAI 兼容代理。客户端请求打到本服务，先校验、再按自定义规则重写请求体，
然后转发给真正的模型供应商，响应原样回传 —— 客户端要流式就给 SSE 流，否则给完整响应。
请求里的 `model` 必须是逻辑模型 id（`models` 段配的），转发给哪个供应商由调用器每次随机挑。

## 文件

    main.py                FastAPI 应用：路由、请求体校验、把聊天请求交给模型调用器、回传响应
    rewrite.py             请求重写钩子 —— 业务逻辑写这里
    model_caller.py        模型调用器：按 provider_id 或逻辑模型调供应商 /chat/completions，原生响应原样返回
    config.py              配置解析：读 ~/.llm-proxy.yaml，提供全局配置对象 CONFIG
    cli.py                 命令行入口：uv tool 装出来的 llm-proxy，只负责把 uvicorn 拉起来
    dev_fake_upstream.py   本地假供应商，测试用，不需要 API 密钥
    README.md              面向使用者的文档
    pyproject.toml         依赖 + 打包配置，用 uv 管理

## 跑起来

    uv sync
    uv run uvicorn main:app --port 8000

启动前要先配好 `~/.llm-proxy.yaml`（providers + models），否则导入 config 时就报错。

不接真实供应商的本地测试（两个终端，yaml 示例见「测试」一节）：

    uv run uvicorn dev_fake_upstream:app --port 9001
    uv run uvicorn main:app --port 8000

装成 uv tool（在任意目录用 `llm-proxy` 启动，命令细节见 README）：

    uv tool install --editable .
    llm-proxy --help

## 打包成 uv tool

`uv tool install` 会按 pyproject.toml 打一个 wheel 装进隔离环境，入口来自 `[project.scripts]`：

    llm-proxy                ->  cli:main             启动代理
    llm-proxy-fake-upstream  ->  cli:fake_upstream    启动假供应商

打包用 hatchling，根目录那几个 .py 靠 `[tool.hatch.build.targets.wheel]` 的 `only-include`
点名（项目没有 src 布局，hatchling 默认会去找叫 llm_proxy 的目录，找不到就报错）。
**不变式**：根目录新增或改名模块时，`only-include` 要同步，否则装出来的工具里缺文件
（报了 `ModuleNotFoundError` 就先查这里）。

改了 `cli.py` 之后重新装一次：

    uv tool install --force --editable .

## 模型调用器与用户配置文件

`model_caller.py` 的 `ModelCaller` 是独立的 HTTP 客户端：给一个 `provider_id` 和一组调用参数，
直接请求供应商的 `/chat/completions`，原生响应（包括错误响应）原样返回；另有 `call_logical()`
收逻辑模型 id，自己随机挑一个候选再交给 `call()`。配置在调用时直接从全局 `CONFIG` 读。

供应商配置不在仓库里，在用户配置文件 `~/.llm-proxy.yaml`（每个用户一份），存在 `providers` 段，
key 是 provider_id：

    providers:
      deepseek:
        base_url: https://api.deepseek.com/v1
        api_key: sk-xxx
        type: DeepSeek

解析这个文件的是 `config.py`：模块导入时读一次，得到全局配置对象 `CONFIG`（dataclass
定义：`Config.providers` 是 `dict[str, ProviderConfig]`，字段用点号取）；文件或 `providers`
段缺失时抛 `ValueError`。`type` 决定请求体怎么重写，目前只认 `DeepSeek` 一个值
（reasoning_effort 转 thinking，规则和 `rewrite.py` 一样）；其它值原样透传，不重写。

同一个文件里还有 `models` 段 —— 逻辑模型列表，key 是逻辑模型 id，值是「供应商id/模型id」列表：

    models:
      my-model:
        - deepseek/deepseek-chat
        - siliconflow/deepseek-ai/DeepSeek-V3

`models` 段可选，缺了就当空表。`call_logical()` 每次调用都重新随机挑一个条目，按第一个 `/`
拆成 供应商id 和 模型id 再发 —— 所以模型 id 里允许再带 `/`。`main.py` 的聊天入口只认
逻辑模型：`model` 不在 `models` 段里就直接 400。

## 转发层的坑（都踩过，别改回去）

1. **读超时必须设成 None**。生成时两个 token 之间可能隔好几分钟，
   普通超时会把长回答从中间掐断。见 `TIMEOUT`。

2. **转发前剥掉这些请求头**（`STRIP_HEADERS`）：逐跳头，加上
   `content-type` / `content-length` / `accept-encoding`。
   前两个由 httpx 重新序列化 JSON 时自己设置；`accept-encoding` 必须让 httpx 自己控制，
   它才能透明解压上游返回的内容。

3. **流式用 `aiter_bytes()`，不要用 `aiter_raw()`**。因为上面剥了 `accept-encoding`、
   由 httpx 负责解压，所以要走解码后的版本。

4. **上游报错绝不走流式。** 先 `aread()` 读完，再作为真正的 HTTP 错误返回。
   否则客户端收到的是一个断掉的 SSE 流，而不是错误信息。

5. **流式响应必须带 `cache-control: no-cache` 和 `x-accel-buffering: no`**，
   否则 nginx 会缓冲 SSE，客户端要等半天才看到第一个字。

6. **客户端断开时要在 `finally` 里 `aclose()` 上游响应**，否则连接池会泄漏。
   见 `relay()`。
