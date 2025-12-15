#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HTTP 客户端管理器 - 复用连接池
"""

from __future__ import annotations

from typing import Optional

import httpx
from loguru import logger

from app.core.config import settings


class HTTPClientManager:
    """HTTP 客户端管理器 - 单例模式，复用连接池"""

    _instance: Optional[HTTPClientManager] = None
    _client: Optional[httpx.AsyncClient] = None

    def __new__(cls) -> HTTPClientManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def get_client(self) -> httpx.AsyncClient:
        """获取 HTTP 客户端（懒加载）"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(settings.HTTP_TIMEOUT),
                limits=httpx.Limits(
                    max_connections=100,
                    max_keepalive_connections=50,
                    keepalive_expiry=30,
                ),
                http2=True,
            )
            logger.info("✅ HTTP 客户端初始化完成")
        return self._client

    async def close(self) -> None:
        """关闭 HTTP 客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
            logger.info("🛑 HTTP 客户端已关闭")


# 全局实例
http_client_manager = HTTPClientManager()
