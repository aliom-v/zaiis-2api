#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import asyncio
import os
from datetime import datetime, timedelta
from loguru import logger
from playwright.async_api import async_playwright
from app.core.config import settings
from app.core.db_manager import db_manager

class TokenAutoRefreshService:
    def __init__(self):
        self.is_running = False
        self.refresh_interval = settings.TOKEN_REFRESH_INTERVAL
        self.preview_mode = False
        self.token_valid_duration = settings.TOKEN_VALID_DURATION
        self.refresh_threshold = settings.TOKEN_REFRESH_THRESHOLD
        
    async def start(self):
        if self.is_running: return
        self.is_running = True
        logger.info("🔄 自动刷新服务启动")
        while self.is_running:
            await self.check_and_refresh_tokens()
            await asyncio.sleep(self.refresh_interval)
    
    def stop(self):
        self.is_running = False
        logger.info("🛑 自动刷新服务停止")
    
    def set_preview_mode(self, enabled: bool):
        self.preview_mode = enabled
        logger.info(f"👁️ 预览模式: {enabled}")

    # --- 核心功能：登录新账号 (Web UI 调用) ---
    async def login_new_account(self, account_name: str):
        """启动有头浏览器，让用户登录，捕获 Token 并保存"""
        logger.info(f"🚀 [WebUI] 启动浏览器登录: {account_name}")
        
        # 1. 准备目录
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dir_name = f"acc_{timestamp}"
        browser_data_dir = os.path.join(settings.ACCOUNTS_DATA_DIR, dir_name, "browser_data")
        os.makedirs(browser_data_dir, exist_ok=True)
        
        browser = None
        context = None
        token = None
        
        try:
            async with async_playwright() as p:
                # 使用 launch_persistent_context 启动有头浏览器
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=browser_data_dir,
                    headless=False,
                    args=["--disable-blink-features=AutomationControlled"]
                )
                page = await context.new_page()
                
                await page.goto("https://zai.is/", wait_until="networkidle")
                logger.info("⏳ 浏览器已打开 zai.is，等待用户登录...")
                logger.info("📝 提示：请在浏览器中完成 Discord 登录，登录成功后会自动检测到 Token")
                
                # 循环检测 Token (5分钟超时)
                for i in range(300):
                    try:
                        # 获取当前URL，判断登录进度
                        current_url = page.url
                        logger.debug(f"[{i+1}/300] 当前URL: {current_url}")
                        
                        # 尝试获取Token
                        token = await page.evaluate("() => localStorage.getItem('token')")
                        
                        if token and len(token) > 50:
                            logger.success(f"✅ 成功捕获到 Token！长度: {len(token)}")
                            logger.info(f"🔑 Token 预览: {token[:20]}...{token[-10:]}")
                            
                            # 尝试获取 Discord cookies（如果有）
                            try:
                                cookies = await context.cookies()
                                discord_cookies = [c for c in cookies if 'discord' in c.get('domain', '')]
                                if discord_cookies:
                                    logger.info(f"🍪 检测到 {len(discord_cookies)} 个 Discord Cookie")
                                    for cookie in discord_cookies[:3]:  # 只显示前3个
                                        logger.debug(f"   - {cookie['name']}: {cookie['value'][:20]}...")
                            except Exception as e:
                                logger.debug(f"获取Cookie失败: {e}")
                            
                            await asyncio.sleep(2)  # 等待数据写入磁盘
                            break
                        
                        # 每10秒输出一次进度
                        if i > 0 and i % 10 == 0:
                            logger.info(f"⏰ 已等待 {i} 秒，请继续在浏览器中完成登录...")
                        
                    except Exception as e:
                        logger.debug(f"检测异常: {e}")
                    
                    await asyncio.sleep(1)
                
                await context.close()
                logger.info("🔒 浏览器已关闭")
                
                if token:
                    # 尝试获取 Discord 用户名
                    discord_username = None
                    try:
                        discord_username = await page.evaluate("""
                            () => {
                                const user = document.querySelector('[class*="username"]');
                                return user ? user.textContent : null;
                            }
                        """)
                    except:
                        pass
                    
                    logger.info(f"📊 账号信息: Token长度={len(token)}, Discord用户={discord_username or '未获取'}")
                    
                    # 存入数据库
                    account_id = db_manager.create_account(
                        name=account_name,
                        token=token,
                        data_dir=dir_name,
                        token_source='browser',
                        discord_username=discord_username or ''
                    )
                    
                    if account_id:
                        logger.success(f"✅ 账号 [{account_name}] 已保存到数据库 (ID: {account_id})")
                        return {"success": True, "message": f"登录成功！账号已保存 (ID: {account_id})", "account_id": account_id}
                    else:
                        logger.error("❌ 数据库保存失败")
                        return {"success": False, "message": "Token已获取但数据库保存失败"}
                else:
                    return {"success": False, "message": "登录超时或未获取到 Token"}
                    
        except Exception as e:
            logger.error(f"浏览器登录出错: {e}")
            return {"success": False, "message": f"浏览器启动失败: {str(e)}"}

    # --- 核心功能：刷新已有账号 ---
    async def check_and_refresh_tokens(self):
        accounts = db_manager.get_all_accounts(active_only=True)
        for acc in accounts:
            if acc['token_source'] != 'browser': continue
            if not acc.get('expires_at'): continue
            
            try:
                exp = datetime.fromisoformat(acc['expires_at'])
                if (exp - datetime.now()).total_seconds() < self.refresh_threshold:
                    logger.info(f"⏳ 账号 {acc['name']} 即将过期，自动刷新...")
                    await self.refresh_token_now(acc['id'])
            except Exception as e:
                logger.error(f"检查账号 {acc['name']} 出错: {e}")

    async def refresh_token_now(self, account_id: int):
        account = db_manager.get_account_by_id(account_id)
        if not account or not account.get('data_dir'): return False
        
        data_dir = os.path.join(settings.ACCOUNTS_DATA_DIR, account['data_dir'], "browser_data")
        if not os.path.exists(data_dir):
            logger.error(f"❌ 数据目录不存在: {data_dir}")
            return False

        logger.info(f"🌐 刷新 Token: {account['name']}")
        try:
            async with async_playwright() as p:
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=data_dir,
                    headless=not self.preview_mode,
                    args=["--disable-blink-features=AutomationControlled"]
                )
                page = await context.new_page()
                try:
                    await page.goto("https://zai.is/", timeout=60000, wait_until="domcontentloaded")
                    token = None
                    for _ in range(10):
                        token = await page.evaluate("() => localStorage.getItem('token')")
                        if token: break
                        await asyncio.sleep(1)
                    
                    if token:
                        db_manager.update_token(account_id, token)
                        logger.success(f"✅ 刷新成功: {account['name']}")
                        return True
                finally:
                    await context.close()
        except Exception as e:
            logger.error(f"刷新失败: {e}")
            return False
        return False

auto_refresh_service = TokenAutoRefreshService()
