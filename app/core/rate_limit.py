#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
请求限流中间件
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Callable, Optional

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.core.errors import create_error_response


class RateLimiter:
    """简单的内存限流器"""

    def __init__(self, requests: int = 60, window: int = 60):
        """
        Args:
            requests: 时间窗口内允许的请求数
            window: 时间窗口（秒）
        """
        self.requests = requests
        self.window = window
        self._requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str) -> tuple[bool, int]:
        """
        检查请求是否被允许

        Returns:
            (是否允许, 剩余请求数)
        """
        now = time.time()
        window_start = now - self.window

        # 清理过期请求
        self._requests[key] = [
            ts for ts in self._requests[key] if ts > window_start
        ]

        # 检查是否超限
        if len(self._requests[key]) >= self.requests:
            return False, 0

        # 记录请求
        self._requests[key].append(now)
        remaining = self.requests - len(self._requests[key])

        return True, remaining

    def get_retry_after(self, key: str) -> int:
        """获取重试等待时间（秒）"""
        if not self._requests[key]:
            return 0

        oldest = min(self._requests[key])
        retry_after = int(oldest + self.window - time.time())
        return max(0, retry_after)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """限流中间件"""

    def __init__(self, app, limiter: Optional[RateLimiter] = None):
        super().__init__(app)
        self.limiter = limiter or RateLimiter(
            requests=settings.RATE_LIMIT_REQUESTS,
            window=settings.RATE_LIMIT_WINDOW,
        )
        self.enabled = settings.RATE_LIMIT_ENABLED

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # 如果限流未启用，直接通过
        if not self.enabled:
            return await call_next(request)

        # 跳过健康检查等端点
        if request.url.path in ["/health", "/", "/v1/models"]:
            return await call_next(request)

        # 获取客户端标识（IP 或 API Key）
        client_key = self._get_client_key(request)

        # 检查限流
        allowed, remaining = self.limiter.is_allowed(client_key)

        if not allowed:
            retry_after = self.limiter.get_retry_after(client_key)
            response = create_error_response(
                message="请求过于频繁，请稍后重试",
                code="RATE_LIMIT",
                status_code=429,
                details={"retry_after": retry_after},
            )
            response.headers["Retry-After"] = str(retry_after)
            response.headers["X-RateLimit-Limit"] = str(self.limiter.requests)
            response.headers["X-RateLimit-Remaining"] = "0"
            return response

        # 处理请求
        response = await call_next(request)

        # 添加限流头
        response.headers["X-RateLimit-Limit"] = str(self.limiter.requests)
        response.headers["X-RateLimit-Remaining"] = str(remaining)

        return response

    def _get_client_key(self, request: Request) -> str:
        """获取客户端标识"""
        # 优先使用 API Key
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return f"key:{auth[7:][:16]}"

        # 使用 IP
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return f"ip:{forwarded.split(',')[0].strip()}"

        client = request.client
        if client:
            return f"ip:{client.host}"

        return "unknown"
