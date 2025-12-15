#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据库管理器 - 统一所有数据库操作
唯一入口，避免锁竞争和数据不一致
"""

from __future__ import annotations

import random
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Optional

from loguru import logger

from app.core.config import settings


class DBManager:
    """数据库管理器 - 单例模式 + 连接池"""

    _instance: Optional[DBManager] = None
    _lock = threading.Lock()

    def __new__(cls) -> DBManager:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.db_path = settings.DB_PATH
        self._db_lock = threading.Lock()
        self._last_used_account_id: Optional[int] = None  # 用于轮询负载均衡
        self._init_database()
        self._initialized = True
        logger.info("✅ 数据库管理器初始化完成")

    @contextmanager
    def _get_connection(self):
        """上下文管理器：获取数据库连接"""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_database(self):
        """初始化数据库表结构"""
        with self._db_lock, self._get_connection() as conn:
            cursor = conn.cursor()

            # 账号表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    data_dir TEXT UNIQUE,
                    token TEXT,
                    token_source TEXT DEFAULT 'manual',
                    created_at TEXT,
                    expires_at TEXT,
                    discord_username TEXT,
                    discord_password TEXT,
                    is_active INTEGER DEFAULT 1,
                    total_calls INTEGER DEFAULT 0,
                    last_used_at TEXT,
                    last_refresh_at TEXT
                )
            """)

            # 日志表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    account_name TEXT,
                    model TEXT,
                    status TEXT,
                    duration INTEGER
                )
            """)

            conn.commit()
            logger.info("✅ 数据库表结构初始化完成")

    # ==================== 账号操作 ====================

    def get_all_accounts(self, active_only: bool = False) -> list[dict]:
        """获取所有账号"""
        with self._db_lock, self._get_connection() as conn:
            cursor = conn.cursor()

            if active_only:
                cursor.execute(
                    "SELECT * FROM accounts WHERE is_active = 1 ORDER BY id ASC"
                )
            else:
                cursor.execute("SELECT * FROM accounts ORDER BY id ASC")

            return [dict(row) for row in cursor.fetchall()]

    def get_account_by_id(self, account_id: int) -> Optional[dict]:
        """根据 ID 获取账号"""
        with self._db_lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM accounts WHERE id = ?", (account_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_next_account(self, strategy: str = "round_robin") -> Optional[dict]:
        """
        获取下一个可用账号（负载均衡）

        Args:
            strategy: 负载均衡策略
                - "round_robin": 轮询（默认）
                - "random": 随机
                - "least_used": 使用次数最少

        Returns:
            账号信息字典，无可用账号返回 None
        """
        with self._db_lock, self._get_connection() as conn:
            cursor = conn.cursor()

            if strategy == "random":
                cursor.execute(
                    "SELECT * FROM accounts WHERE is_active = 1 ORDER BY RANDOM() LIMIT 1"
                )
            elif strategy == "least_used":
                cursor.execute(
                    "SELECT * FROM accounts WHERE is_active = 1 ORDER BY total_calls ASC LIMIT 1"
                )
            else:  # round_robin
                if self._last_used_account_id:
                    cursor.execute(
                        """SELECT * FROM accounts
                           WHERE is_active = 1 AND id > ?
                           ORDER BY id ASC LIMIT 1""",
                        (self._last_used_account_id,),
                    )
                    row = cursor.fetchone()
                    if not row:
                        # 回到开头
                        cursor.execute(
                            "SELECT * FROM accounts WHERE is_active = 1 ORDER BY id ASC LIMIT 1"
                        )
                        row = cursor.fetchone()
                else:
                    cursor.execute(
                        "SELECT * FROM accounts WHERE is_active = 1 ORDER BY id ASC LIMIT 1"
                    )
                    row = cursor.fetchone()

                if row:
                    self._last_used_account_id = row["id"]
                    return dict(row)
                return None

            row = cursor.fetchone()
            if row:
                if strategy == "round_robin":
                    self._last_used_account_id = row["id"]
                return dict(row)
            return None

    def create_account(
        self,
        name: str,
        token: str,
        data_dir: Optional[str],
        token_source: str = "browser",
        discord_username: str = "",
    ) -> Optional[int]:
        """创建账号"""
        with self._db_lock, self._get_connection() as conn:
            cursor = conn.cursor()

            created_at = datetime.now().isoformat()
            expires_at = (datetime.now() + timedelta(hours=3)).isoformat()

            try:
                cursor.execute(
                    """
                    INSERT INTO accounts
                    (name, token, data_dir, token_source, created_at, expires_at, discord_username, is_active)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                """,
                    (
                        name,
                        token,
                        data_dir,
                        token_source,
                        created_at,
                        expires_at,
                        discord_username,
                    ),
                )

                account_id = cursor.lastrowid
                conn.commit()

                logger.success(f"创建账号成功: {name} (ID: {account_id})")
                return account_id

            except sqlite3.IntegrityError as e:
                logger.error(f"创建账号失败: {e}")
                return None

    def update_token(self, account_id: int, token: str) -> None:
        """更新账号 Token"""
        with self._db_lock, self._get_connection() as conn:
            cursor = conn.cursor()

            expires_at = (datetime.now() + timedelta(hours=3)).isoformat()
            now = datetime.now().isoformat()

            cursor.execute(
                """
                UPDATE accounts
                SET token = ?, expires_at = ?, last_refresh_at = ?, is_active = 1
                WHERE id = ?
            """,
                (token, expires_at, now, account_id),
            )

            conn.commit()
            logger.info(f"更新Token成功: ID {account_id}")

    def update_stats(self, account_id: int) -> None:
        """更新账号统计"""
        with self._db_lock, self._get_connection() as conn:
            cursor = conn.cursor()

            now = datetime.now().isoformat()
            cursor.execute(
                """
                UPDATE accounts
                SET total_calls = total_calls + 1, last_used_at = ?
                WHERE id = ?
            """,
                (now, account_id),
            )

            conn.commit()

    def disable_account(self, account_id: int) -> None:
        """禁用账号"""
        with self._db_lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE accounts SET is_active = 0 WHERE id = ?", (account_id,)
            )
            conn.commit()
            logger.info(f"禁用账号: ID {account_id}")

    def delete_account(self, account_id: int) -> None:
        """删除账号"""
        with self._db_lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            conn.commit()
            logger.info(f"删除账号: ID {account_id}")

    def toggle_account(self, account_id: int) -> None:
        """切换账号状态"""
        with self._db_lock, self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT is_active FROM accounts WHERE id = ?", (account_id,))
            row = cursor.fetchone()

            if row:
                new_status = 0 if row[0] else 1
                cursor.execute(
                    "UPDATE accounts SET is_active = ? WHERE id = ?",
                    (new_status, account_id),
                )
                conn.commit()

                status_text = "启用" if new_status else "禁用"
                logger.info(f"{status_text}账号: ID {account_id}")

    # ==================== 日志操作 ====================

    def add_log(
        self,
        account_name: str,
        model: str,
        status: str,
        duration: int,
        message: Optional[str] = None,
    ) -> None:
        """添加日志"""
        with self._db_lock, self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                INSERT INTO logs (timestamp, account_name, model, status, duration)
                VALUES (?, ?, ?, ?, ?)
            """,
                (datetime.now().isoformat(), account_name, model, status, duration),
            )

            conn.commit()

    def get_recent_logs(self, limit: int = 20) -> list[dict]:
        """获取最近日志"""
        with self._db_lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def clear_logs(self) -> None:
        """清空日志"""
        with self._db_lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM logs")
            conn.commit()
            logger.info("日志已清空")

    # ==================== 统计方法 ====================

    def get_stats(self) -> dict:
        """获取统计信息"""
        with self._db_lock, self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM accounts")
            total_accounts = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM accounts WHERE is_active = 1")
            active_accounts = cursor.fetchone()[0]

            cursor.execute("SELECT SUM(total_calls) FROM accounts")
            total_calls = cursor.fetchone()[0] or 0

            return {
                "total_accounts": total_accounts,
                "active_accounts": active_accounts,
                "inactive_accounts": total_accounts - active_accounts,
                "total_calls": total_calls,
            }


# 全局实例
db_manager = DBManager()
