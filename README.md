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

想把它当成命令行工具、在任意目录启动，见下面「装成 uv tool」一节。

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

## 装成 uv tool

装成 uv tool 之后，代码进一个独立的虚拟环境，命令放进 `~/.local/bin`，在任何目录都能启动，
和项目里的 `.venv` 互不影响：

    uv tool install --editable .      # 推荐：改了 rewrite.py 重启一下就生效
    llm-proxy                         # 默认 127.0.0.1:8000

    llm-proxy --port 9000
    llm-proxy --host 0.0.0.0              # 想让局域网里别的机器连进来
    llm-proxy --reload                    # 改代码自动重启
    llm-proxy --env-file ~/proxy.env      # 配置不在当前目录时，指定 .env 的路径

不写 `--env-file` 的话，`main.py` 里的 `load_dotenv` 会读当前工作目录（以及上级目录）的
`.env` —— 你在哪个目录启动，就读哪个目录的配置。

不想和源码目录绑定（比如以后把仓库挪走也要能用），去掉 `--editable`，装一份代码快照：

    uv tool install .                 # 冻结当前代码
    uv tool install --force .         # 改完代码后要重新装一次才生效
    uv tool uninstall llm-proxy       # 卸载

Windows 上重新安装之前，先把正在跑的 `llm-proxy` 关掉，否则 `Scripts` 目录被占用，
会报 `os error 5（拒绝访问）`。

假供应商也一起装了，不接真实上游时可以直接冒烟测试：

    llm-proxy-fake-upstream           # 默认 127.0.0.1:9001
    UPSTREAM_BASE_URL=http://127.0.0.1:9001/v1 llm-proxy

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
