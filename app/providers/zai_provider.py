#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Zai Provider - 负责与 Zai.is API 通信

架构原则：
- 接收 Token，执行 HTTP 请求
- 不管理 Token 生命周期
- 不操作浏览器
- 只负责与 Zai API 通信
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import AsyncGenerator, Optional

import httpx
from loguru import logger

from app.core.config import settings
from app.core.http_client import http_client_manager
from app.providers.base_provider import BaseProvider
from app.utils.sse_utils import create_chat_completion_chunk

# 模型显示名称映射
MODEL_DISPLAY_NAMES: dict[str, str] = {
    "gemini-3-pro-image-preview": "Nano Banana Pro",
    "gemini-2.5-pro": "Gemini 2.5 Pro",
    "claude-opus-4-20250514": "Claude Opus 4",
    "claude-sonnet-4-5-20250929": "Claude Sonnet 4.5",
    "claude-sonnet-4-20250514": "Claude Sonnet 4",
    "claude-haiku-4-5-20251001": "Claude Haiku 4.5",
    "o1-2024-12-17": "o1",
    "o3-pro-2025-06-10": "o3-pro",
    "grok-4-1-fast-reasoning": "Grok 4.1 Fast",
    "grok-4-0709": "Grok 4",
    "o4-mini-2025-04-16": "o4-mini",
    "gpt-5-2025-08-07": "GPT-5",
    "gemini-2.5-flash-image": "Nano Banana",
}

# 默认请求头
DEFAULT_HEADERS: dict[str, str] = {
    "Accept": "*/*",
    "Origin": "https://zai.is",
    "Referer": "https://zai.is/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
}


class ZaiProvider(BaseProvider):
    """Zai Provider - 负责与 Zai.is API 通信"""

    def __init__(
        self,
        base_url: Optional[str] = None,
        default_model: Optional[str] = None,
        max_retries: Optional[int] = None,
        retry_delay: Optional[float] = None,
    ):
        self.base_url = base_url or settings.ZAI_BASE_URL
        self.default_model = default_model or settings.DEFAULT_MODEL
        self.max_retries = max_retries or settings.HTTP_MAX_RETRIES
        self.retry_delay = retry_delay or settings.HTTP_RETRY_DELAY

    def _get_headers(self, token: str) -> dict[str, str]:
        """获取请求头"""
        return {
            **DEFAULT_HEADERS,
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def verify_token_async(self, token: str) -> bool:
        """异步验证 Token 是否有效"""
        if not token or len(token) < 50:
            return False

        headers = {
            **DEFAULT_HEADERS,
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.base_url}/api/v1/chats/?page=1",
                    headers=headers,
                )
                return resp.status_code == 200
        except Exception as e:
            logger.error(f"Token验证失败: {e}")
            return False

    def verify_token(self, token: str) -> bool:
        """同步验证 Token（兼容旧代码）"""
        if not token or len(token) < 50:
            return False

        try:
            import cloudscraper

            scraper = cloudscraper.create_scraper()
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": DEFAULT_HEADERS["User-Agent"],
            }
            resp = scraper.get(
                f"{self.base_url}/api/v1/chats/?page=1",
                headers=headers,
                timeout=10,
            )
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"Token验证失败: {e}")
            return False

    async def _request_with_retry(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        **kwargs,
    ) -> httpx.Response:
        """带重试机制的请求"""
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries):
            try:
                if method == "POST":
                    resp = await client.post(url, **kwargs)
                else:
                    resp = await client.get(url, **kwargs)

                # 非 5xx 错误直接返回
                if resp.status_code < 500:
                    return resp

                # 5xx 错误重试
                logger.warning(f"请求返回 {resp.status_code}，第 {attempt + 1} 次重试...")

            except (httpx.ConnectError, httpx.ReadTimeout) as e:
                last_error = e
                logger.warning(f"请求失败: {e}，第 {attempt + 1} 次重试...")

            if attempt < self.max_retries - 1:
                await asyncio.sleep(self.retry_delay * (attempt + 1))

        raise last_error or Exception("请求失败，已达最大重试次数")

    async def chat_completion(
        self, request_data: dict, token: str
    ) -> AsyncGenerator[str, None]:
        """
        聊天完成接口 - 遵循 Zai.is API 流程

        流程：
        1. POST /api/v1/chats/new - 创建对话
        2. POST /api/chat/completions - 流式请求 AI 回复
        """
        if not token:
            yield f"data: {json.dumps({'error': 'No token provided'})}\n\n"
            return

        model = request_data.get("model", self.default_model)
        messages = request_data.get("messages", [])

        if not messages:
            yield f"data: {json.dumps({'error': 'No messages provided'})}\n\n"
            return

        # 构造消息
        user_msg_id = str(uuid.uuid4())
        assistant_msg_id = str(uuid.uuid4())
        timestamp = int(time.time())
        user_content = messages[-1]["content"]
        model_name = MODEL_DISPLAY_NAMES.get(model, model)

        zai_messages = self._build_zai_messages(
            user_msg_id, assistant_msg_id, user_content, model, model_name, timestamp
        )

        headers = self._get_headers(token)

        # 使用全局 HTTP 客户端
        client = await http_client_manager.get_client()

        try:
            # 步骤1：创建新对话
            chat_id = await self._create_chat(
                client, headers, zai_messages, assistant_msg_id, model, timestamp
            )

            if not chat_id:
                yield f"data: {json.dumps({'error': 'Token无效或已过期'})}\n\n"
                return

            # 步骤2：流式补全
            async for chunk in self._stream_completion(
                client, headers, model, user_content
            ):
                yield chunk

        except Exception as e:
            logger.error(f"API请求失败: {e}")
            error_chunk = create_chat_completion_chunk(
                "error", model, f"Error: {str(e)}"
            )
            yield f"data: {json.dumps(error_chunk)}\n\n"
            yield "data: [DONE]\n\n"

    def _build_zai_messages(
        self,
        user_msg_id: str,
        assistant_msg_id: str,
        user_content: str,
        model: str,
        model_name: str,
        timestamp: int,
    ) -> dict:
        """构建 Zai.is 格式的消息对象"""
        return {
            user_msg_id: {
                "id": user_msg_id,
                "parentId": None,
                "childrenIds": [assistant_msg_id],
                "role": "user",
                "content": user_content,
                "timestamp": timestamp,
                "models": [model],
            },
            assistant_msg_id: {
                "parentId": user_msg_id,
                "id": assistant_msg_id,
                "childrenIds": [],
                "role": "assistant",
                "content": "",
                "model": model,
                "modelName": model_name,
                "modelIdx": 0,
                "timestamp": timestamp,
            },
        }

    async def _create_chat(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        zai_messages: dict,
        assistant_msg_id: str,
        model: str,
        timestamp: int,
    ) -> Optional[str]:
        """创建新对话，返回 chat_id"""
        payload = {
            "chat": {
                "id": "",
                "title": "新对话",
                "models": [model],
                "params": {},
                "history": {
                    "messages": zai_messages,
                    "currentId": assistant_msg_id,
                },
                "messages": list(zai_messages.values()),
                "tags": [],
                "timestamp": timestamp * 1000,
            },
            "folder_id": None,
        }

        resp = await self._request_with_retry(
            client,
            "POST",
            f"{self.base_url}/api/v1/chats/new",
            json=payload,
            headers=headers,
        )

        if resp.status_code == 401:
            return None

        resp.raise_for_status()
        chat_data = resp.json()
        chat_id = chat_data.get("id", "")
        logger.info(f"对话创建成功: {chat_id[:8]}... ({model})")
        return chat_id

    async def _stream_completion(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        model: str,
        user_content: str,
    ) -> AsyncGenerator[str, None]:
        """流式获取 AI 回复"""
        payload = {
            "stream": True,
            "model": model,
            "messages": [{"role": "user", "content": user_content, "extensions": {}}],
            "params": {},
            "tool_servers": [],
            "features": {
                "image_generation": False,
                "code_interpreter": False,
                "web_search": False,
            },
            "variables": {
                "{{CURRENT_DATETIME}}": time.strftime("%Y-%m-%d %H:%M:%S"),
                "{{CURRENT_DATE}}": time.strftime("%Y-%m-%d"),
                "{{CURRENT_TIME}}": time.strftime("%H:%M:%S"),
                "{{CURRENT_WEEKDAY}}": time.strftime("%A"),
                "{{CURRENT_TIMEZONE}}": "Asia/Shanghai",
                "{{USER_LANGUAGE}}": "zh-CN",
            },
        }

        async with client.stream(
            "POST",
            f"{self.base_url}/api/chat/completions",
            json=payload,
            headers=headers,
        ) as resp:
            request_id = f"chatcmpl-{uuid.uuid4()}"
            full_content = ""

            async for line in resp.aiter_lines():
                if not line or line.startswith(":"):
                    continue

                if line.startswith("data: "):
                    data_str = line[6:]

                    if data_str == "[DONE]":
                        break

                    try:
                        chunk_data = json.loads(data_str)
                        content = self._extract_content(chunk_data)

                        if content:
                            full_content += content
                            openai_chunk = create_chat_completion_chunk(
                                request_id, model, content
                            )
                            yield f"data: {json.dumps(openai_chunk)}\n\n"
                    except json.JSONDecodeError:
                        pass

            # 发送结束标记
            final_chunk = create_chat_completion_chunk(request_id, model, "", "stop")
            yield f"data: {json.dumps(final_chunk)}\n\n"
            yield "data: [DONE]\n\n"

            logger.info(f"AI响应完成，共 {len(full_content)} 字符")

    def _extract_content(self, chunk_data: dict) -> str:
        """从响应数据中提取内容"""
        # OpenAI 格式
        if "choices" in chunk_data and chunk_data["choices"]:
            delta = chunk_data["choices"][0].get("delta", {})
            return delta.get("content", "")

        # Zai 格式
        return chunk_data.get("content", "")
