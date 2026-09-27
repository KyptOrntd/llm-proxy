from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProxyRequest:
    model: str
    messages: list
    reasoning_effort: str | None
    stream: bool
    extra: dict


async def rewrite_request(req: ProxyRequest) -> None:
    effort = req.reasoning_effort
    if effort is None or effort == "none":
        req.extra["thinking"] = {"type": "disabled"}
        req.reasoning_effort = None
    else:
        req.extra["thinking"] = {"type": "enabled"}
