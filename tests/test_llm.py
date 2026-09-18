"""LLMClient 错误矩阵的离线测试：所有失败都必须包装为 GenerationError。"""

import json

import pytest
import requests

import repo2gal.llm as llm_module
from repo2gal.errors import GenerationError
from repo2gal.llm import LLMClient, MISSING_KEY_MESSAGE


class FakeResponse:
    def __init__(
        self,
        *,
        ok=True,
        status_code=200,
        text="",
        payload=None,
        json_fail=False,
        content_type="application/json",
        stream_lines=None,
    ):
        self.ok = ok
        self.status_code = status_code
        self.text = text
        self.headers = {"Content-Type": content_type}
        self._payload = payload
        self._json_fail = json_fail
        self._stream_lines = list(stream_lines or [])
        self.closed = False

    def json(self):
        if self._json_fail:
            raise ValueError("not json")
        return self._payload

    def iter_lines(self):
        for line in self._stream_lines:
            yield line.encode("utf-8")

    def close(self):
        self.closed = True


def sse(*events) -> list[str]:
    """把若干事件拼成 OpenAI 兼容的 SSE 行（结尾补 [DONE]）。"""
    lines = [f"data: {json.dumps(event, ensure_ascii=False)}" for event in events]
    lines.append("data: [DONE]")
    return lines


def client(**kwargs):
    defaults = {"base_url": "https://example.test/v1", "model": "m", "api_key": "sk-secret-123"}
    defaults.update(kwargs)
    return LLMClient(**defaults)


def test_complete_success(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs["json"]
        return FakeResponse(payload={"choices": [{"message": {"content": "剧本"}}]})

    monkeypatch.setattr(llm_module.requests, "post", fake_post)
    assert client().complete("prompt") == "剧本"
    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["json"]["messages"] == [{"role": "user", "content": "prompt"}]


def test_missing_api_key_raises_generation_error(monkeypatch):
    monkeypatch.delenv("REPO2GAL_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(GenerationError) as exc:
        client(api_key=None).complete("prompt")
    assert "REPO2GAL_API_KEY" in str(exc.value)


def test_network_error_wrapped_and_redacted(monkeypatch):
    def fake_post(url, **kwargs):
        raise requests.ConnectionError("连接 https://user:sk-secret-123@example.test/v1 失败")

    monkeypatch.setattr(llm_module.requests, "post", fake_post)
    with pytest.raises(GenerationError) as exc:
        client().complete("prompt")
    message = str(exc.value)
    assert "sk-secret-123" not in message  # 密钥不出现在任何输出
    assert "LLM 请求失败" in message


def test_http_error_wrapped_and_redacted(monkeypatch):
    monkeypatch.setattr(
        llm_module.requests,
        "post",
        lambda *a, **kw: FakeResponse(
            ok=False, status_code=401, text='{"error":"invalid key sk-secret-123"}'
        ),
    )
    with pytest.raises(GenerationError) as exc:
        client().complete("prompt")
    message = str(exc.value)
    assert "401" in message
    assert "sk-secret-123" not in message


def test_non_json_response_wrapped(monkeypatch):
    monkeypatch.setattr(
        llm_module.requests, "post", lambda *a, **kw: FakeResponse(json_fail=True)
    )
    with pytest.raises(GenerationError) as exc:
        client().complete("prompt")
    assert "JSON" in str(exc.value)


def test_malformed_structure_wrapped(monkeypatch):
    monkeypatch.setattr(
        llm_module.requests, "post", lambda *a, **kw: FakeResponse(payload={"choices": []})
    )
    with pytest.raises(GenerationError) as exc:
        client().complete("prompt")
    assert "响应结构异常" in str(exc.value)


def test_non_string_content_wrapped(monkeypatch):
    monkeypatch.setattr(
        llm_module.requests,
        "post",
        lambda *a, **kw: FakeResponse(payload={"choices": [{"message": {"content": 123}}]}),
    )
    with pytest.raises(GenerationError) as exc:
        client().complete("prompt")
    assert "不是字符串" in str(exc.value)


# --- 流式（默认路径）：长回答靠持续吐字节避免网关按响应时长掐断 ---


def test_complete_streams_sse_chunks(monkeypatch):
    captured = {}
    response = FakeResponse(
        content_type="text/event-stream; charset=utf-8",
        stream_lines=sse(
            {"choices": [{"delta": {"content": "第一段"}}]},
            {"choices": [{"delta": {"reasoning_content": "思考字段按设计忽略"}}]},
            {"choices": [{"delta": {"content": "第二段"}}]},
        ),
    )

    def fake_post(url, **kwargs):
        captured["json"] = kwargs["json"]
        captured["stream"] = kwargs.get("stream")
        return response

    monkeypatch.setattr(llm_module.requests, "post", fake_post)
    assert client().complete("prompt") == "第一段第二段"
    assert captured["json"]["stream"] is True
    assert captured["stream"] is True
    assert response.closed is True


def test_complete_stream_error_event_wrapped(monkeypatch):
    response = FakeResponse(
        content_type="text/event-stream",
        stream_lines=sse({"error": {"message": "rate limited sk-secret-123"}}),
    )
    monkeypatch.setattr(llm_module.requests, "post", lambda *a, **kw: response)
    with pytest.raises(GenerationError) as exc:
        client().complete("prompt")
    message = str(exc.value)
    assert "流式返回错误" in message
    assert "sk-secret-123" not in message


def test_complete_stream_without_content_wrapped(monkeypatch):
    response = FakeResponse(content_type="text/event-stream", stream_lines=["data: [DONE]"])
    monkeypatch.setattr(llm_module.requests, "post", lambda *a, **kw: response)
    with pytest.raises(GenerationError) as exc:
        client().complete("prompt")
    assert "没有返回任何内容" in str(exc.value)


def test_complete_falls_back_when_gateway_ignores_stream(monkeypatch):
    """网关忽略 stream=true、直接回整包 JSON 时仍要能解析。"""
    monkeypatch.setattr(
        llm_module.requests,
        "post",
        lambda *a, **kw: FakeResponse(payload={"choices": [{"message": {"content": "整包"}}]}),
    )
    assert client().complete("prompt") == "整包"