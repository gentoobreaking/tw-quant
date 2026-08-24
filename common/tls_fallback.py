"""TWCA TLS 備援共用模組

背景：「TWCA Global Root CA」憑證缺 Subject Key Identifier，
OpenSSL 3.x 嚴格檢查會拒絕其簽發的整條鏈（TDCC、MoneyDJ 等台灣網站常見）。
處理：以內建的「TWCA Secure SSL 中繼憑證」（有 SKI）作為 trust anchor
（VERIFY_X509_PARTIAL_CHAIN），保留完整簽章與主機名稱驗證。

注意：requests 在 https_proxy 環境下走 ProxyManager，
必須同時覆寫 proxy_manager_for 才能讓自訂 context 生效。
"""
from __future__ import annotations

import ssl
from pathlib import Path

import requests

from .logger import logger

TWCA_INTERMEDIATE = Path(__file__).resolve().parent / "certs" / "twca-intermediate.pem"
TDCC_SSL_ERR_MARKER = "Missing Subject Key Identifier"


def twca_ssl_context() -> ssl.SSLContext:
    """以 TWCA Secure SSL 中繼憑證為 trust anchor 的 context"""
    ctx = ssl.create_default_context(cafile=str(TWCA_INTERMEDIATE))
    # 中繼憑證非自簽根，需 PARTIAL_CHAIN 才能作為鏈終點
    ctx.verify_flags |= getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0x80000)
    return ctx


class TwcaAdapter(requests.adapters.HTTPAdapter):
    """掛載 TWCA anchor context 的 adapter（直連＋代理路徑都要吃）"""

    def __init__(self, tls_ctx: ssl.SSLContext, **kw):
        self._tls_ctx = tls_ctx
        super().__init__(**kw)

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = self._tls_ctx
        super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, proxy, **proxy_kwargs):
        proxy_kwargs["ssl_context"] = self._tls_ctx
        return super().proxy_manager_for(proxy, **proxy_kwargs)


def make_twca_session(headers: dict | None = None) -> requests.Session:
    """建立「一律使用 TWCA anchor」的 session（給已知會踩雷的主機用）"""
    s = requests.Session()
    if headers:
        s.headers.update(headers)
    s.mount("https://", TwcaAdapter(twca_ssl_context()))
    return s


def is_missing_ski_error(e: Exception) -> bool:
    return TDCC_SSL_ERR_MARKER in str(e)
