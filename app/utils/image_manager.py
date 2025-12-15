#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图片管理器 - 处理 Base64 图片保存和清理
"""

import asyncio
import base64
import os
import secrets
from datetime import datetime, timedelta

from loguru import logger


class ImageManager:
    """图片管理器：处理 Base64 图片的保存和定期清理"""

    def __init__(self, media_dir: str = "media"):
        self.media_dir = media_dir
        if not os.path.exists(self.media_dir):
            os.makedirs(self.media_dir)
        self.cleanup_task = None

    def start_cleanup_task(self):
        """启动定时清理任务"""
        if self.cleanup_task is None:
            try:
                self.cleanup_task = asyncio.create_task(self.cleanup_old_images())
            except RuntimeError:
                logger.warning("没有运行的事件循环，稍后启动清理任务")

    async def cleanup_old_images(self):
        """定期清理30分钟前的图片"""
        while True:
            try:
                await asyncio.sleep(60 * 30)  # 每30分钟检查一次
                now = datetime.now()
                cleaned_count = 0

                for filename in os.listdir(self.media_dir):
                    file_path = os.path.join(self.media_dir, filename)
                    if os.path.isfile(file_path):
                        file_time = datetime.fromtimestamp(os.path.getmtime(file_path))
                        if now - file_time > timedelta(minutes=30):
                            try:
                                os.remove(file_path)
                                cleaned_count += 1
                            except Exception as e:
                                logger.error(f"删除旧图片失败 {file_path}: {e}")

                if cleaned_count > 0:
                    logger.info(f"🧹 已清理 {cleaned_count} 张过期图片")

            except Exception as e:
                logger.error(f"清理图片任务出错: {e}")

    def save_base64_image(self, base64_data: str) -> str:
        """
        保存 base64 图片并返回文件名

        Args:
            base64_data: Base64 编码的图片数据（可带或不带 data URI 前缀）

        Returns:
            保存后的文件名
        """
        # 移除 base64 前缀并确定扩展名
        ext = "png"  # 默认扩展名

        if base64_data.startswith("data:image"):
            header, base64_data = base64_data.split(",", 1)
            ext_mapping = {
                "jpeg": "jpg",
                "jpg": "jpg",
                "png": "png",
                "gif": "gif",
                "webp": "webp",
            }
            for key, value in ext_mapping.items():
                if key in header:
                    ext = value
                    break

        # 生成唯一文件名
        filename = f"{secrets.token_urlsafe(16)}.{ext}"
        filepath = os.path.join(self.media_dir, filename)

        # 解码并保存图片
        image_data = base64.b64decode(base64_data)
        with open(filepath, "wb") as f:
            f.write(image_data)

        return filename

    def get_image_path(self, filename: str) -> str:
        """获取图片完整路径"""
        return os.path.join(self.media_dir, filename)


# 全局实例
image_manager = ImageManager()
