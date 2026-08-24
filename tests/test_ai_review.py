"""AI 評估模組測試 — 環境變數解析、呼叫、失敗容錯、快取回補"""
import json
from unittest.mock import MagicMock, patch

import pytest

from common.ai_review import ai_evaluate, resolve_ai_config


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """隔離真實環境變數，確保測試可重現"""
    for v in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL"):
        monkeypatch.delenv(v, raising=False)


BASE_CFG = {
    "enabled": True,
    "base_url": "https://api.example.com/v1",
    "model": "test-model",
    "api_key": "key-cfg",
    "temperature": 0.2,
}


# ---- resolve_ai_config：環境變數優先 ----
def test_resolve_env_overrides_config(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    monkeypatch.setenv("OPENAI_MODEL", "qwen2.5:14b")
    r = resolve_ai_config(BASE_CFG)
    assert r["base_url"] == "http://localhost:11434/v1"
    assert r["api_key"] == "env-key"
    assert r["model"] == "qwen2.5:14b"
    assert r["enabled"] is True


def test_auto_enable_when_env_complete(monkeypatch):
    """三個環境變數齊備即自動啟用（config 無需 enabled 鍵）"""
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("OPENAI_MODEL", "qwen2.5:14b")
    r = resolve_ai_config({})                      # config 完全沒設定
    assert r["enabled"] is True                    # 自動生效
    assert r["api_key"] == ""                      # 本地端點可無金鑰


def test_explicit_false_forces_off(monkeypatch):
    """明確 enabled: false → 即使環境變數齊備也關閉"""
    monkeypatch.setenv("OPENAI_BASE_URL", "http://x/v1")
    monkeypatch.setenv("OPENAI_MODEL", "m")
    r = resolve_ai_config(dict(BASE_CFG, enabled=False))
    assert r["enabled"] is False


def test_resolve_falls_back_to_config(monkeypatch):
    for v in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL"):
        monkeypatch.delenv(v, raising=False)
    r = resolve_ai_config(BASE_CFG)
    assert r["base_url"] == "https://api.example.com/v1"
    assert r["model"] == "test-model"


def test_resolve_disabled_when_missing():
    # 環境變數與 config 都沒有端點 → 不啟用
    r = resolve_ai_config({"enabled": True})
    assert r["enabled"] is False
    r = resolve_ai_config({})
    assert r["enabled"] is False


# ---- ai_evaluate ----
ROW = {"ticker": "3037", "name": "欣興", "grade": "S", "total": 84,
       "f2": 30, "rr": 4.02, "close": 1075.0}


def _mock_post(status=200, content="數據一致，無額外風險"):
    resp = MagicMock()
    resp.status_code = status
    if status == 200:
        resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    else:
        resp.text = f"error {status}"
        resp.json.side_effect = ValueError
    return resp


def test_ai_evaluate_success():
    with patch("requests.post", return_value=_mock_post()) as mp:
        out = ai_evaluate(ROW, BASE_CFG)
    assert out == "數據一致，無額外風險"
    # 驗證送出的 payload
    kwargs = mp.call_args.kwargs
    body = kwargs["json"]
    assert body["model"] == "test-model"
    assert any("風控覆核" in m["content"] for m in body["messages"])
    assert "3037" in body["messages"][-1]["content"]
    assert kwargs["headers"]["Authorization"] == "Bearer key-cfg"


def test_ai_evaluate_failure_returns_placeholder():
    cfg = dict(BASE_CFG, retries=2)
    with patch("requests.post", return_value=_mock_post(status=500)), \
         patch("common.ai_review.time.sleep"):
        out = ai_evaluate(ROW, cfg)
    assert out.startswith("—（AI 未回應")
    assert "已重試" in out
    assert "500" in out


def test_ai_evaluate_disabled():
    cfg = dict(BASE_CFG, enabled=False)
    assert ai_evaluate(ROW, cfg) == "—（AI 評估未啟用或未設定端點）"


def test_ai_evaluate_cache_complete_vs_retry(tmp_path):
    from common.cache import DiskCache
    cache = DiskCache(str(tmp_path / "t.db"), ttl=7200)

    # 順序：第一次失敗、第二次成功
    responses = [RuntimeError("斷線"), _mock_post(content="第二次成功")]
    def fake_post(*a, **kw):
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    with patch("requests.post", side_effect=fake_post):
        out1 = ai_evaluate(ROW, BASE_CFG, cache=cache)   # 失敗 → 不入快取
        out2 = ai_evaluate(ROW, BASE_CFG, cache=cache)   # 重試成功 → 入快取
        out3 = ai_evaluate(ROW, BASE_CFG, cache=cache)   # 走快取，不再呼叫

    assert len(responses) == 0            # 兩個 mock 回應都用掉
    assert out1.startswith("—（AI 未回應")
    assert out2 == "第二次成功"
    assert out3 == "第二次成功"           # 快取命中
