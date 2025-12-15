#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置管理 - 使用 Pydantic Settings
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置"""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # === 应用信息 ===
    APP_NAME: str = "zai-2api"
    APP_VERSION: str = "2.1.0"

    # === API 配置 ===
    API_MASTER_KEY: str = "1"
    PORT: int = 8000

    # === 路径配置 ===
    BASE_DIR: str = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    DB_PATH: str = os.path.join(BASE_DIR, "data", "zai.db")
    ACCOUNTS_DATA_DIR: str = os.path.join(BASE_DIR, "accounts_data")

    # === Zai 配置 ===
    ZAI_BASE_URL: str = "https://zai.is"
    DEFAULT_MODEL: str = "gpt-5-2025-08-07"

    # === 性能配置 ===
    # 负载均衡策略: round_robin, random, least_used
    LOAD_BALANCE_STRATEGY: Literal["round_robin", "random", "least_used"] = "round_robin"

    # HTTP 客户端配置
    HTTP_TIMEOUT: int = 120  # 请求超时（秒）
    HTTP_MAX_RETRIES: int = 3  # 最大重试次数
    HTTP_RETRY_DELAY: float = 1.0  # 重试延迟（秒）

    # === 限流配置 ===
    RATE_LIMIT_ENABLED: bool = False  # 是否启用限流
    RATE_LIMIT_REQUESTS: int = 60  # 每分钟请求数
    RATE_LIMIT_WINDOW: int = 60  # 时间窗口（秒）

    # === Token 刷新配置 ===
    TOKEN_REFRESH_INTERVAL: int = 3600  # Token 刷新间隔（秒）
    TOKEN_VALID_DURATION: int = 10800  # Token 有效期（秒）
    TOKEN_REFRESH_THRESHOLD: int = 3600  # Token 刷新阈值（秒）

    # === 日志配置 ===
    LOG_LEVEL: str = "INFO"


settings = Settings()
