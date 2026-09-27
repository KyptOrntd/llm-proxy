"""本地冒烟测试用的假供应商 —— 不需要真实 API 密钥，不花钱。

终端 1：
    uv run uvicorn dev_fake_upstream:app --port 9001
终端 2：
    UPSTREAM_BASE_URL=http://127.0.0.1:9001/v1 uv run uvicorn main:app --port 8000

它会把代理实际发过来的请求（model、messages、authorization 头）echo 回去，
所以重写有没有生效，直接看模型输出就知道。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="fake-upstream")


def _seen(body: dict, headers) -> dict:
    # 把代理实际发过来的请求体原样列出来，方便肉眼核对重写结果。
    # 某个键不存在时这里就不会出现它 —— 正好能看出重写有没有把参数删掉。
    seen = {}
    for key in body:
        if key == "messages":
            # messages 太长会把输出撑乱，只显示条数。
            value = body[key]
            if isinstance(value, list):
                seen[key] = "<" + str(len(value)) + " 条>"
            else:
                seen[key] = value
            continue
        seen[key] = body[key]
    seen["authorization"] = headers.get("authorization")
    return seen


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    model = body.get("model", "fake-model")
    created = int(time.time())
    text = "echo: " + json.dumps(_seen(body, request.headers), ensure_ascii=False)
    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

    # 消息里带上 BOOM 就故意报错，用来测代理的错误转发分支。
    if "BOOM" in json.dumps(body.get("messages", [])):
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "forced upstream failure", "type": "invalid_request_error"}},
        )

    request_id = "fake-req-" + uuid.uuid4().hex[:8]

    if not body.get("stream"):
        return JSONResponse(
            headers={"x-request-id": request_id},
            content={
                "id": chunk_id,
                "object": "chat.completion",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    async def events():
        # 一个词一个 chunk 地吐，中间 sleep 一下，模拟真实的流式生成节奏。
        for word in text.split(" "):
            chunk = {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {"index": 0, "delta": {"content": word + " "}, "finish_reason": None}
                ],
            }
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()
            await asyncio.sleep(0.05)
        final = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        yield f"data: {json.dumps(final, ensure_ascii=False)}\n\n".encode()
        yield b"data: [DONE]\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"x-request-id": request_id},
    )


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": "fake-model", "object": "model"}]}
