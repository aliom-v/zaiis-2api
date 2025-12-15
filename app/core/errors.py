#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一错误处理
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import Request
from fastapi.responses import JSONResponse


class APIError(Exception):
    """API 错误基类"""

    def __init__(
        self,
        message: str,
        code: str = "ERROR",
        status_code: int = 500,
        details: Optional[dict] = None,
    ):
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details or {}
        super().__init__(self.message)


class ValidationError(APIError):
    """验证错误"""

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, "VALIDATION_ERROR", 400, details)


class AuthenticationError(APIError):
    """认证错误"""

    def __init__(self, message: str = "认证失败"):
        super().__init__(message, "AUTH_ERROR", 401)


class NotFoundError(APIError):
    """资源未找到"""

    def __init__(self, message: str = "资源未找到"):
        super().__init__(message, "NOT_FOUND", 404)


class RateLimitError(APIError):
    """请求限流"""

    def __init__(self, message: str = "请求过于频繁，请稍后重试"):
        super().__init__(message, "RATE_LIMIT", 429)


class ServiceUnavailableError(APIError):
    """服务不可用"""

    def __init__(self, message: str = "服务暂时不可用"):
        super().__init__(message, "SERVICE_UNAVAILABLE", 503)


class NoAvailableAccountError(ServiceUnavailableError):
    """无可用账号"""

    def __init__(self):
        super().__init__("没有可用账号")


def create_error_response(
    message: str,
    code: str = "ERROR",
    status_code: int = 500,
    details: Optional[dict] = None,
) -> JSONResponse:
    """创建统一错误响应"""
    content = {
        "success": False,
        "error": {
            "code": code,
            "message": message,
        },
    }
    if details:
        content["error"]["details"] = details

    return JSONResponse(content=content, status_code=status_code)


async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    """API 错误处理器"""
    return create_error_response(
        message=exc.message,
        code=exc.code,
        status_code=exc.status_code,
        details=exc.details,
    )


def create_success_response(
    data: Any = None, message: str = "success"
) -> dict:
    """创建统一成功响应"""
    response = {"success": True, "message": message}
    if data is not None:
        response["data"] = data
    return response
