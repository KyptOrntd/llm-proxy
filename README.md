# llm-proxy

极简的 OpenAI 兼容代理。客户端请求打到本服务的 `/v1/chat/completions`，你先对请求做重写，
代理再把请求转发给真正的模型供应商，并把响应原样回传 —— 客户端要流式就给 SSE 流，
否则给完整响应。

    main.py                FastAPI 应用：路由、请求体校验、请求头处理、转发
    rewrite.py             重写钩子 —— 你要改的就是这个文件
    dev_fake_upstream.py   本地测试用的假供应商（不需要 API 密钥，不花钱）
    AGENTS.md              给编码助手看的项目上下文
    pyproject.toml         项目元数据和依赖
    uv.lock                锁定的依赖版本 —— 建议提交到版本库
    .env.example

## 安装

    uv sync                   # 创建 .venv，并按 uv.lock 精确安装依赖

## 启动

    cp .env.example .env      # 填好 UPSTREAM_BASE_URL / UPSTREAM_API_KEY
    uv run uvicorn main:app --port 8000

`.env` 由 python-dotenv 自动加载，不用加 `--env-file` 之类的参数。
已经存在的环境变量不会被 `.env` 覆盖，所以临时换个上游直接命令行传就行：

    UPSTREAM_BASE_URL=http://127.0.0.1:9001/v1 uv run uvicorn main:app --port 8000

`uv run` 会自己找到虚拟环境，不需要手动激活。想加依赖：

    uv add <包名>

想生成普通的 requirements 文件（给 Docker 或 CI 用）：

    uv export --no-hashes --no-dev > requirements.txt

任何 OpenAI 客户端都可以直接指向它：

    from openai import OpenAI
    client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="随便填")

## 重写逻辑

所有要改的东西都写在 `rewrite.py` 里。`ProxyRequest` 只暴露请求体里常用的四个字段，
其余字段统一放在 `extra`：

    req.model             等价于 body["model"]
    req.messages          等价于 body["messages"]
    req.reasoning_effort  等价于 body["reasoning_effort"]，设成 None 表示不发给上游
    req.stream            等价于 body["stream"]
    req.extra             其余所有字段，比如 temperature、tools、max_tokens 等

    async def rewrite_request(req: ProxyRequest) -> None:
        req.model = "gpt-4o-mini"
        req.extra["temperature"] = 0
        req.messages.insert(0, {"role": "system", "content": "..."})

同步函数和 async 函数都可以。抛 `fastapi.HTTPException` 可以拒绝这次请求。
只改请求体 —— 请求头、上游地址这些由 `main.py` 管，不在这里。

## 不接真实供应商也能测

    # 终端 1
    uv run uvicorn dev_fake_upstream:app --port 9001
    # 终端 2
    UPSTREAM_BASE_URL=http://127.0.0.1:9001/v1 uv run uvicorn main:app --port 8000

假供应商会把收到的请求原样 echo 回来，所以重写有没有生效，直接看模型输出就知道。
消息里带上 `BOOM` 这三个字母，它会返回 400，用来测错误分支。

## 行为说明

- 设置了 `UPSTREAM_API_KEY` -> 每次调用上游都用它。没设置 -> 把客户端自己的
  `Authorization` 请求头原样转发。
- 上游返回错误时，会先读完再作为真正的 HTTP 错误回传，绝不会变成断掉的流。
- 流式是真透传：chunk 到达即转发（实测到达时间戳间隔约 62ms、整体约 1.1s，
  与上游自身的节奏一致），不会攒完再发。客户端断开时会关闭上游连接。
- 转发前会剥掉逐跳头以及 `content-type` / `content-length` / `accept-encoding`，
  因为这些由 httpx 重新生成。
