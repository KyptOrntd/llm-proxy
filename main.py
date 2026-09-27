"""OpenAI 兼容的聊天补全代理 —— FastAPI + httpx。

POST /v1/chat/completions 的处理流程：
    1. 解析客户端发来的 JSON 请求体
    2. 校验请求体（validate_chat_request），不合法直接返回 400
    3. 把请求体拆成 ProxyRequest，交给 rewrite.py 里的重写钩子处理
    4. 把重写后的字段拼回请求体，转发给真正的模型供应商
    5. 把响应原样回传给客户端 —— 客户端要流式就给 SSE 流，否则给完整响应

环境变量（用 python-dotenv 从 .env 读取，也可以用命令行直接传）：
    UPSTREAM_BASE_URL   供应商 API 根地址，结尾不要带斜杠（默认 https://api.openai.com/v1）
    UPSTREAM_API_KEY    调用上游时使用的密钥；不设置的话，就把客户端自己的
                        Authorization 请求头原样转发过去

启动（.env 会自动加载，不需要额外参数）：
    uv run uvicorn main:app --reload --port 8000

冒烟测试：
    curl -N http://127.0.0.1:8000/v1/chat/completions \
      -H 'Content-Type: application/json' -H 'Authorization: Bearer sk-test' \
      -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}],"stream":true}'
"""

import inspect
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from rewrite import ProxyRequest, rewrite_request

# 先把 .env 里的变量读进 os.environ，再读下面这些配置。
# load_dotenv 默认不覆盖已经存在的环境变量，所以命令行上传的值优先级更高。
load_dotenv()

UPSTREAM_BASE_URL = os.environ.get(
    "UPSTREAM_BASE_URL", "https://api.openai.com/v1"
).rstrip("/")
UPSTREAM_API_KEY = os.environ.get("UPSTREAM_API_KEY", "").strip()

# 生成过程中两个 token 之间可能停顿好几分钟，所以读超时必须设成永不超时。
TIMEOUT = httpx.Timeout(connect=10.0, read=None, write=60.0, pool=10.0)

# 转发前要丢掉的请求头：逐跳（hop-by-hop）头，以及所有我们要自己重新生成的头。
# httpx 会根据我们重新序列化后的 JSON 自己设置 content-type 和 content-length；
# accept-encoding 也必须由 httpx 自己控制，它才能透明地解压供应商返回的内容。
STRIP_HEADERS = [
    "host",
    "content-length",
    "content-type",
    "accept-encoding",
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
]

# 供应商返回了这些响应头时，原样带回给客户端。
PASS_THROUGH_RESPONSE_HEADERS = [
    "x-request-id",
    "openai-processing-ms",
    "openai-version",
]

# 客户端指定的 reasoning_effort 只允许这些取值，其余一律拒绝。
ALLOWED_REASONING_EFFORT = [
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
]

# ProxyRequest 里单独拎出来的字段，请求体里其余字段统一进 extra。
PROXY_REQUEST_FIELDS = [
    "model",
    "messages",
    "reasoning_effort",
    "stream",
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 整个应用共用一个 httpx 客户端，可以复用连接池；应用关闭时统一释放。
    app.state.client = httpx.AsyncClient(timeout=TIMEOUT)
    try:
        yield
    finally:
        await app.state.client.aclose()


app = FastAPI(title="llm-proxy", lifespan=lifespan)


def upstream_headers(incoming) -> dict:
    """复制客户端的请求头，丢掉那些不能转发的。"""
    headers = {}
    for key, value in incoming.items():
        if key.lower() in STRIP_HEADERS:
            continue
        headers[key] = value
    if UPSTREAM_API_KEY:
        headers["authorization"] = "Bearer " + UPSTREAM_API_KEY
    return headers


def response_headers(upstream: httpx.Response) -> dict:
    """只保留值得回传给客户端的供应商响应头。"""
    headers = {}
    for key, value in upstream.headers.items():
        if key.lower() not in PASS_THROUGH_RESPONSE_HEADERS:
            continue
        headers[key] = value
    return headers


async def relay(upstream: httpx.Response) -> AsyncIterator[bytes]:
    """把上游响应体原样透传出去，客户端断开连接时顺手关闭上游连接。"""
    try:
        async for chunk in upstream.aiter_bytes():
            yield chunk
    finally:
        await upstream.aclose()


def validate_chat_request(body: dict) -> None:
    effort = body.get("reasoning_effort")
    if effort is not None:
        if effort not in ALLOWED_REASONING_EFFORT:
            raise HTTPException(
                status_code=400,
                detail="reasoning_effort 只能是 "
                + "、".join(ALLOWED_REASONING_EFFORT)
                + " 中的一个，收到的是："
                + repr(effort),
            )


def build_proxy_request(body: dict) -> ProxyRequest:
    extra = {}
    for key in body:
        if key in PROXY_REQUEST_FIELDS:
            continue
        extra[key] = body[key]

    return ProxyRequest(
        model=body.get("model", ""),
        messages=body.get("messages", []),
        reasoning_effort=body.get("reasoning_effort"),
        stream=bool(body.get("stream")),
        extra=extra,
    )


def build_request_body(req: ProxyRequest) -> dict:
    """把重写后的 ProxyRequest 拼回请求体，发给上游。

    reasoning_effort 是 None 时不写进请求体，也就是「这个参数不发给上游」。
    stream 是 False 时同理 —— 不传 stream 和传 false，对上游来说是一样的。
    """
    body = {}
    for key in req.extra:
        body[key] = req.extra[key]
    body["model"] = req.model
    body["messages"] = req.messages
    if req.reasoning_effort is not None:
        body["reasoning_effort"] = req.reasoning_effort
    if req.stream:
        body["stream"] = True
    return body


@app.get("/health")
async def health() -> dict:
    # 用来快速确认服务活着、以及当前指向哪个上游。
    return {"status": "ok", "upstream": UPSTREAM_BASE_URL}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体必须是合法的 JSON")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体必须是一个 JSON 对象")

    validate_chat_request(body)

    req = build_proxy_request(body)

    # --- 自定义重写钩子：rewrite.py ------------------------------------------
    outcome = rewrite_request(req)
    if inspect.isawaitable(outcome):
        await outcome
    # -------------------------------------------------------------------------

    client = request.app.state.client
    try:
        upstream_request = client.build_request(
            "POST",
            UPSTREAM_BASE_URL + "/chat/completions",
            headers=upstream_headers(request.headers),
            json=build_request_body(req),
        )
        upstream = await client.send(upstream_request, stream=True)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail="请求上游失败：" + str(exc))

    # 错误绝不走流式 —— 先读出来，这样客户端能收到一个正常的 JSON 错误。
    if upstream.status_code >= 400:
        content = await upstream.aread()
        headers = response_headers(upstream)
        content_type = upstream.headers.get("content-type", "application/json")
        status_code = upstream.status_code
        await upstream.aclose()
        return Response(
            content=content,
            status_code=status_code,
            media_type=content_type,
            headers=headers,
        )

    if req.stream:
        headers = response_headers(upstream)
        headers["cache-control"] = "no-cache"
        headers["x-accel-buffering"] = "no"  # 防止 nginx 缓冲 SSE 流
        return StreamingResponse(
            relay(upstream),
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "text/event-stream"),
            headers=headers,
        )

    content = await upstream.aread()
    headers = response_headers(upstream)
    content_type = upstream.headers.get("content-type", "application/json")
    status_code = upstream.status_code
    await upstream.aclose()
    return Response(
        content=content,
        status_code=status_code,
        media_type=content_type,
        headers=headers,
    )


@app.get("/v1/models")
async def list_models(request: Request):
    """纯透传 —— 客户端经常先探测这个接口，来验证地址和密钥能不能用。"""
    client = request.app.state.client
    try:
        upstream = await client.get(
            UPSTREAM_BASE_URL + "/models", headers=upstream_headers(request.headers)
        )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail="请求上游失败：" + str(exc))

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
    )
