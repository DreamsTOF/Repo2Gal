"""LLM transport 薄客户端：OpenAI-compatible Chat Completions。

刻意不锁定厂商：任何兼容该协议的服务（DeepSeek、Kimi、本地 vLLM 等）都可通过
base_url 接入。本模块只负责网络与响应解析，并把所有失败统一包装成
:class:`~repo2gal.errors.GenerationError`（CLI 退出码 4），错误正文一律脱敏。
叙事 prompt 的组装在 ``generator.py``，重试策略刻意不在此实现（保持 v0.2.0
行为，需要时再单独调研依赖）。

请求一律走流式（``stream=true``）：第三轮导演 JSON 动辄上万 token，非流式请求在
网关侧按"响应时长"被掐断（实测 HTTP 524），流式让字节持续流动即可规避；同时兼容
忽略 ``stream`` 参数、直接返回整包 JSON 的网关。
"""

from __future__ import annotations

import json

import requests

from .config import DEFAULT_LLM_TIMEOUT, resolve_api_key
from .errors import GenerationError, redact_error

MISSING_KEY_MESSAGE = "缺少 API Key，请设置环境变量 REPO2GAL_API_KEY"


class LLMClient:
    """一次运行复用一个客户端；complete() 是唯一入口。"""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: int = DEFAULT_LLM_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def complete(self, prompt: str, *, temperature: float = 0.8) -> str:
        """调用一次 Chat Completions 并返回 content 字符串（流式聚合）。

        ``timeout`` 在流式下按"两次数据之间"计时，长回答不会因为总时长被判定超时。
        """
        api_key = self.api_key or resolve_api_key()
        if not api_key:
            raise GenerationError(MISSING_KEY_MESSAGE)
        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": temperature,
                    "stream": True,
                },
                timeout=self.timeout,
                stream=True,
            )
        except requests.RequestException as exc:
            raise GenerationError(
                f"LLM 请求失败：{redact_error(str(exc), secret=api_key)}"
            ) from exc
        try:
            if not resp.ok:
                raise GenerationError(
                    f"LLM 返回 {resp.status_code}：{redact_error(resp.text, secret=api_key)}"
                )
            content_type = (resp.headers.get("Content-Type") or "").lower()
            if "text/event-stream" in content_type:
                return self._stream_content(resp, api_key)
            # 网关忽略 stream：退化成整包 JSON 解析
            try:
                data = resp.json()
            except ValueError as exc:
                raise GenerationError("LLM 响应不是合法 JSON") from exc
            return self._message_content(data)
        finally:
            resp.close()

    @staticmethod
    def _message_content(data: object) -> str:
        try:
            content = data["choices"][0]["message"]["content"]  # type: ignore[index]
        except (KeyError, IndexError, TypeError) as exc:
            raise GenerationError("LLM 响应结构异常：缺少 choices[0].message.content") from exc
        if not isinstance(content, str):
            raise GenerationError("LLM 响应 content 不是字符串")
        return content

    @staticmethod
    def _stream_content(resp: requests.Response, api_key: str) -> str:
        """聚合 SSE 分片；``delta.reasoning_content`` 等思考字段按设计忽略。"""
        chunks: list[str] = []
        for raw in resp.iter_lines():
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[len("data:") :].strip()
            if payload == "[DONE]":
                break
            try:
                event = json.loads(payload)
            except ValueError:
                continue
            if isinstance(event, dict) and isinstance(event.get("error"), dict):
                detail = redact_error(json.dumps(event["error"], ensure_ascii=False), secret=api_key)
                raise GenerationError(f"LLM 流式返回错误：{detail}")
            choices = event.get("choices") if isinstance(event, dict) else None
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            piece = delta.get("content")
            if isinstance(piece, str):
                chunks.append(piece)
        if not chunks:
            raise GenerationError("LLM 流式响应没有返回任何内容")
        return "".join(chunks)
