# AGENTS.md

给在这个仓库里干活的编码助手看的上下文。面向使用者的说明在 `README.md`。

## 项目是什么

极简的 OpenAI 兼容代理。客户端请求打到本服务，先校验、再按自定义规则重写请求体，
然后转发给真正的模型供应商，响应原样回传 —— 客户端要流式就给 SSE 流，否则给完整响应。

## 文件

    main.py                FastAPI 应用：路由、请求体校验、请求头处理、转发
    rewrite.py             请求重写钩子 —— 业务逻辑写这里
    model_caller.py        模型调用器：按 provider_id 调供应商 /chat/completions，原生响应原样返回
    cli.py                 命令行入口：uv tool 装出来的 llm-proxy，只负责把 uvicorn 拉起来
    dev_fake_upstream.py   本地假供应商，测试用，不需要 API 密钥
    README.md              面向使用者的文档
    pyproject.toml         依赖 + 打包配置，用 uv 管理
    .env.example           环境变量模板

## 跑起来

    uv sync
    cp .env.example .env
    uv run uvicorn main:app --port 8000

不接真实供应商的本地测试（两个终端）：

    uv run uvicorn dev_fake_upstream:app --port 9001
    UPSTREAM_BASE_URL=http://127.0.0.1:9001/v1 uv run uvicorn main:app --port 8000

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
直接请求供应商的 `/chat/completions`，原生响应（包括错误响应）原样返回。

供应商配置不在仓库里，在用户配置文件 `~/.llm-proxy.yaml`（每个用户一份），存在 `providers` 段，
key 是 provider_id：

    providers:
      deepseek:
        base_url: https://api.deepseek.com/v1
        api_key: sk-xxx
        type: DeepSeek

`ModelCaller()` 构造时用 `load_providers()` 读这个文件（路径固定在 `~/.llm-proxy.yaml`）；
文件或 `providers` 段缺失时抛 `ValueError`。`type` 决定请求体怎么重写，目前只认 `DeepSeek`
一个值（reasoning_effort 转 thinking，规则和 `rewrite.py` 一样）；其它值原样透传，不重写。

## 代码规范

作者是中文母语者，读英文吃力。下面几条必须遵守。

1. **注释和 docstring 写中文，但要精简。** 一两行说清就行，别写大段解释性文字。
   变量名、函数名、技术名词（httpx、SSE、content-type 这类）保持英文。

2. **只用基础 Python 语法：**
   - 不用推导式，写显式的 `for` 加 `if ... continue`
   - 不用 f-string，用 `+` 拼接字符串
   - 不用字典解包 `{**a, ...}`，显式逐个赋值
   - 类型标注从简，写 `dict` 而不是 `dict[str, str]`
   - 例外：dataclass 的字段必须带标注（dataclasses 的要求），`rewrite.py` 因此
     保留了 `from __future__ import annotations`，用来支持 `str | None` 这种写法

3. **分层严格：**
   - 请求体校验 → `main.py` 的 `validate_chat_request`
   - 请求体重写 → `rewrite.py` 的 `rewrite_request`
   - 不要让重写钩子承担校验职责，也不要把校验混进重写逻辑

## 请求流程

入口是 `main.py` 的 `chat_completions`：

    解析 JSON
      -> validate_chat_request    校验，不合法直接 400
      -> build_proxy_request      把请求体拆成 ProxyRequest
      -> rewrite_request          重写（在 rewrite.py 里）
      -> build_request_body       把字段拼回请求体
      -> 转发上游
      -> 回传响应（流式 / 非流式）

## ProxyRequest 的形状

`rewrite.py` 里只暴露请求体里这五个字段：

    req.model             body["model"]
    req.messages          body["messages"]
    req.reasoning_effort  body["reasoning_effort"]，None 表示这个参数不发给上游
    req.stream            body["stream"]
    req.extra             其余所有字段（temperature、tools、max_tokens ...）

`req.reasoning_effort = None` 的语义是「这个参数不发给上游」，由 `main.py` 的
`build_request_body` 落实 —— 它不把这个键写进请求体。

**不变式**：`main.py` 的 `PROXY_REQUEST_FIELDS` 必须和 `ProxyRequest` 的字段一一对应。
改了一边另一边要同步。涉及三处联动：

    main.py    PROXY_REQUEST_FIELDS
    main.py    build_proxy_request
    main.py    build_request_body

## 当前的重写逻辑

把 OpenAI 的 `reasoning_effort` 翻译成供应商要的 `thinking`：

    reasoning_effort 是 None 或 "none"
        -> extra["thinking"] = {"type": "disabled"}，reasoning_effort 清空
    其它合法值
        -> extra["thinking"] = {"type": "enabled"}，reasoning_effort 原样保留

`thinking` 不是 `ProxyRequest` 的字段，所以放进 `extra`，会被一起发往上游。

合法取值在 `main.py` 的 `ALLOWED_REASONING_EFFORT`：

    none / minimal / low / medium / high / xhigh / max

校验在 `main.py` 做，`rewrite.py` 里假定值已经合法，不再重复判断。

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

## 测试

`dev_fake_upstream.py` 会把代理实际发过去的整个请求体 echo 回来（messages 只显示条数）。
所以重写有没有生效，直接看模型输出就知道 —— 哪个键被删了、哪个键多出来了，一眼可见。

- 消息里带上 `BOOM` → 假上游返回 400，用来测错误转发分支
- 端口：假上游 9001，代理 8000
- 想看代理发出去的完整请求体：

      curl -s http://127.0.0.1:8000/v1/chat/completions \
        -H 'Content-Type: application/json' \
        -d '{"model":"m","messages":[{"role":"user","content":"hi"}],"reasoning_effort":"high"}'

## 常见改动改哪里

    加校验规则          main.py  ->  validate_chat_request
    加重写规则          rewrite.py  ->  rewrite_request
    改请求体字段拆分    main.py 那三处 + rewrite.py 的 ProxyRequest 一起改
    改转发行为          main.py  ->  upstream_headers / response_headers / relay
    加新接口            main.py，照 /v1/models 的写法
    加命令行入口        cli.py + pyproject.toml 的 [project.scripts]，别忘 only-include
    改模型调用器        model_caller.py（供应商配置在 ~/.llm-proxy.yaml）
