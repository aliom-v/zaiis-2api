#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Zai-2API: 将 Zai.is 转换为 OpenAI 兼容 API 的代理服务
"""

import asyncio
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

import httpx
from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger

from app.core.config import settings
from app.core.db_manager import db_manager
from app.core.errors import (
    APIError,
    NoAvailableAccountError,
    api_error_handler,
    create_error_response,
    create_success_response,
)
from app.core.http_client import http_client_manager
from app.core.rate_limit import RateLimitMiddleware
from app.providers.zai_provider import ZaiProvider
from app.utils.har_parser import extract_token_from_text
from app.utils.image_manager import image_manager
from app.utils.token_auto_refresh_service import auto_refresh_service

# --- 全局 Provider ---
provider = ZaiProvider()

# --- 启动时间 ---
_start_time = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"🚀 {settings.APP_NAME} v{settings.APP_VERSION} 启动中...")

    # 1. 启动时检查过期 Token
    asyncio.create_task(perform_breakpoint_update())

    # 2. 启动自动刷新服务
    asyncio.create_task(auto_refresh_service.start())

    # 3. 启动图片管理清理任务
    image_manager.start_cleanup_task()

    logger.info(f"🌐 服务地址: http://localhost:{settings.PORT}")
    yield

    # 停止服务
    auto_refresh_service.stop()
    await http_client_manager.close()
    logger.info("🛑 服务已停止")


app = FastAPI(
    lifespan=lifespan,
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="将 Zai.is 转换为 OpenAI 兼容 API 的代理服务",
)

# 添加限流中间件
app.add_middleware(RateLimitMiddleware)

# 添加错误处理器
app.add_exception_handler(APIError, api_error_handler)

templates = Jinja2Templates(directory="templates")

# 创建静态文件目录（如果不存在）
static_dir = os.path.join(os.getcwd(), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# 为 Zai 图片创建别名（用于处理 /media/ 路径）
media_dir = os.path.join(os.getcwd(), "media")
os.makedirs(media_dir, exist_ok=True)
app.mount("/media", StaticFiles(directory=media_dir), name="media")

# 图片代理端点 - 处理 Zai 图片的跨域问题
@app.get("/img-proxy")
async def img_proxy(url: str):
    """
    图片代理端点，用于处理 Zai 图片的跨域问题
    """
    try:
        # 验证URL是否为Zai的图片URL
        if not url.startswith(('https://zai.is/media/', 'http://zai.is/media/')):
            # 如果不是Zai的图片，检查是否是其他外部图片URL
            if url.startswith(('http://', 'https://')):
                # 对于外部图片URL，也进行代理处理
                pass
            else:
                # 如果不是URL格式，返回错误
                return JSONResponse({"error": "无效的图片URL"}, status_code=400)
        
        # 下载图片
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=30)
            response.raise_for_status()
            
            # 获取内容类型
            content_type = response.headers.get('content-type', 'image/jpeg')
            
            # 返回图片
            from fastapi.responses import Response
            return Response(
                content=response.content,
                media_type=content_type,
                headers={
                    "Cache-Control": "public, max-age=3600",  # 缓存1小时
                    "Access-Control-Allow-Origin": "*",      # 允许跨域访问
                    "Access-Control-Allow-Methods": "GET, OPTIONS",   # 允许GET和OPTIONS方法
                    "Access-Control-Allow-Headers": "*",      # 允许所有头部
                    "Access-Control-Allow-Credentials": "false"  # 不包含凭据
                }
            )
    except httpx.HTTPStatusError as e:
        logger.error(f"图片代理错误 - HTTP状态码: {e.response.status_code}")
        # 返回一个默认图片或错误
        return JSONResponse({"error": f"无法加载图片 - 状态码: {e.response.status_code}"}, status_code=404)
    except Exception as e:
        logger.error(f"图片代理错误: {e}")
        # 返回一个默认图片或错误
        return JSONResponse({"error": "无法加载图片"}, status_code=404)

# --- 鉴权 ---
async def verify_api_key(authorization: str = Header(None)):
    if settings.API_MASTER_KEY and settings.API_MASTER_KEY != "1":
        if not authorization or authorization.split(" ")[1] != settings.API_MASTER_KEY:
            raise HTTPException(status_code=403, detail="Invalid API Key")

# --- 页面路由 ---
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    accounts = db_manager.get_all_accounts()
    logs = db_manager.get_recent_logs()
    
    active_count = len([acc for acc in accounts if acc["is_active"]])
    inactive_count = len(accounts) - active_count
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "api_url": f"http://localhost:{settings.PORT}",
        "accounts": accounts,
        "active_count": active_count,
        "inactive_count": inactive_count,
        "logs": logs
    })

# --- API 路由 (账号管理) ---
@app.post("/api/account/login/start")
async def start_browser_login(name: str = Form(...)):
    """
    [核心功能] Web UI 触发浏览器登录
    """
    logger.info(f"🌐 Web UI 请求启动浏览器登录: {name}")
    
    # 检查重名
    accounts = db_manager.get_all_accounts()
    for acc in accounts:
        if acc['name'] == name:
            return JSONResponse(status_code=400, content={"success": False, "message": "账号名称已存在"})

    # 调用 Service 启动有头浏览器
    # 注意：这里使用 await 会阻塞 HTTP 请求直到登录完成（或超时）
    # 对于本地单人使用是完全可以的，能直接拿到结果
    result = await auto_refresh_service.login_new_account(name)
    
    return JSONResponse(result)

@app.post("/api/account/add")
async def add_account(name: str = Form(...), token: str = Form(...)):
    """手动添加 Token"""
    if not provider.verify_token(token):
        return JSONResponse(status_code=400, content={"success": False, "message": "Token 无效"})
    
    account_id = db_manager.create_account(name, token, None, 'manual')
    if account_id:
        return JSONResponse({"success": True, "message": "账号添加成功"})
    return JSONResponse(status_code=500, content={"success": False, "message": "数据库错误"})

@app.post("/api/account/extract")
async def extract_token_api(request: Request):
    data = await request.json()
    token = extract_token_from_text(data.get("content", ""))
    if token:
        return JSONResponse({"success": True, "token": token, "is_valid": provider.verify_token(token)})
    return JSONResponse({"success": False, "message": "未找到 Token"})

@app.get("/api/account/delete/{id}")
async def delete_account(id: int):
    db_manager.delete_account(id)
    return RedirectResponse("/", status_code=303)

@app.get("/api/account/toggle/{id}")
async def toggle_account(id: int):
    db_manager.toggle_account(id)
    return RedirectResponse("/", status_code=303)

@app.get("/api/logs/clear")
async def clear_logs():
    db_manager.clear_logs()
    return RedirectResponse("/", status_code=303)

# --- API 路由 (Anthropic 兼容 - 用于 Claude Code CLI) ---
@app.post("/v1/messages", dependencies=[Depends(verify_api_key)])
async def anthropic_messages(request: Request):
    """
    Anthropic Messages API 兼容端点
    支持 Claude Code CLI 等使用 Anthropic API 格式的客户端
    """
    start_time = time.time()
    try:
        request_data = await request.json()
    except:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    model = request_data.get("model", "claude-sonnet-4-5-20250929")
    messages = request_data.get("messages", [])
    stream = request_data.get("stream", False)
    max_tokens = request_data.get("max_tokens", 4096)

    # 模型名称映射：Anthropic 模型名 -> Zai 模型名
    model_mapping = {
        # Claude 4 系列
        "claude-opus-4-20250514": "claude-opus-4-20250514",
        "claude-sonnet-4-20250514": "claude-sonnet-4-20250514",
        "claude-sonnet-4-5-20250929": "claude-sonnet-4-5-20250929",
        "claude-haiku-4-5-20251001": "claude-haiku-4-5-20251001",
        # Claude 3.5 系列 -> 映射到 Claude 4
        "claude-3-5-sonnet-20241022": "claude-sonnet-4-5-20250929",
        "claude-3-5-sonnet-latest": "claude-sonnet-4-5-20250929",
        "claude-3-5-haiku-20241022": "claude-haiku-4-5-20251001",
        "claude-3-5-haiku-latest": "claude-haiku-4-5-20251001",
        # Claude 3 系列 -> 映射到 Claude 4
        "claude-3-opus-20240229": "claude-opus-4-20250514",
        "claude-3-opus-latest": "claude-opus-4-20250514",
        "claude-3-sonnet-20240229": "claude-sonnet-4-20250514",
        "claude-3-haiku-20240307": "claude-haiku-4-5-20251001",
        # 通用别名
        "opus": "claude-opus-4-20250514",
        "sonnet": "claude-sonnet-4-5-20250929",
        "haiku": "claude-haiku-4-5-20251001",
    }

    # 映射模型名称
    zai_model = model_mapping.get(model, model)

    accounts = db_manager.get_all_accounts(active_only=True)
    if not accounts:
        raise HTTPException(status_code=503, detail="没有可用账号")

    # 转换为 OpenAI 格式的请求
    openai_request = {
        "model": zai_model,
        "messages": messages,
        "stream": True,  # 内部始终使用流式
        "max_tokens": max_tokens
    }

    for account in accounts:
        try:
            if stream:
                # 流式响应 - Anthropic SSE 格式
                async def anthropic_stream_generator():
                    import uuid
                    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
                    input_tokens = sum(len(m.get("content", "")) for m in messages) // 4
                    output_tokens = 0

                    # message_start 事件
                    message_start = {
                        "type": "message_start",
                        "message": {
                            "id": msg_id,
                            "type": "message",
                            "role": "assistant",
                            "content": [],
                            "model": model,
                            "stop_reason": None,
                            "stop_sequence": None,
                            "usage": {"input_tokens": input_tokens, "output_tokens": 0}
                        }
                    }
                    yield f"event: message_start\ndata: {json.dumps(message_start)}\n\n"

                    # content_block_start 事件
                    content_block_start = {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""}
                    }
                    yield f"event: content_block_start\ndata: {json.dumps(content_block_start)}\n\n"

                    # 调用 ZaiProvider 获取响应
                    full_content = ""
                    async for chunk in provider.chat_completion(openai_request, account["token"]):
                        if chunk.startswith("data: "):
                            data_str = chunk[6:].strip()
                            if data_str == "[DONE]":
                                continue
                            try:
                                chunk_data = json.loads(data_str)
                                if "choices" in chunk_data and chunk_data["choices"]:
                                    delta = chunk_data["choices"][0].get("delta", {})
                                    content = delta.get("content", "")
                                    if content:
                                        full_content += content
                                        output_tokens += len(content) // 4
                                        # content_block_delta 事件
                                        content_delta = {
                                            "type": "content_block_delta",
                                            "index": 0,
                                            "delta": {"type": "text_delta", "text": content}
                                        }
                                        yield f"event: content_block_delta\ndata: {json.dumps(content_delta)}\n\n"
                            except json.JSONDecodeError:
                                pass

                    # content_block_stop 事件
                    content_block_stop = {"type": "content_block_stop", "index": 0}
                    yield f"event: content_block_stop\ndata: {json.dumps(content_block_stop)}\n\n"

                    # message_delta 事件
                    message_delta = {
                        "type": "message_delta",
                        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                        "usage": {"output_tokens": max(output_tokens, 1)}
                    }
                    yield f"event: message_delta\ndata: {json.dumps(message_delta)}\n\n"

                    # message_stop 事件
                    yield f"event: message_stop\ndata: {{\"type\": \"message_stop\"}}\n\n"

                duration = int((time.time() - start_time) * 1000)
                db_manager.add_log(account["name"], zai_model, "SUCCESS", duration)
                return StreamingResponse(anthropic_stream_generator(), media_type="text/event-stream")

            else:
                # 非流式响应
                import uuid
                msg_id = f"msg_{uuid.uuid4().hex[:24]}"
                full_content = ""

                async for chunk in provider.chat_completion(openai_request, account["token"]):
                    if chunk.startswith("data: "):
                        data_str = chunk[6:].strip()
                        if data_str == "[DONE]":
                            continue
                        try:
                            chunk_data = json.loads(data_str)
                            if "choices" in chunk_data and chunk_data["choices"]:
                                delta = chunk_data["choices"][0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    full_content += content
                        except json.JSONDecodeError:
                            pass

                input_tokens = sum(len(m.get("content", "")) for m in messages) // 4
                output_tokens = len(full_content) // 4

                response = {
                    "id": msg_id,
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": full_content}],
                    "model": model,
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": input_tokens,
                        "output_tokens": max(output_tokens, 1)
                    }
                }

                duration = int((time.time() - start_time) * 1000)
                db_manager.add_log(account["name"], zai_model, "SUCCESS", duration)
                return JSONResponse(response)

        except Exception as e:
            logger.error(f"账号 {account['name']} 失败: {e}")
            db_manager.add_log(account["name"], zai_model, "ERROR", int((time.time() - start_time) * 1000))
            continue

    raise HTTPException(status_code=503, detail="所有账号均调用失败")


# --- API 路由 (OpenAI 兼容) ---
@app.post("/v1/chat/completions", dependencies=[Depends(verify_api_key)])
async def chat_completions(request: Request):
    start_time = time.time()
    try:
        request_data = await request.json()
    except:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    model = request_data.get("model", settings.DEFAULT_MODEL)

    # 使用负载均衡获取账号（最多重试3次）
    max_retries = 3
    for attempt in range(max_retries):
        account = db_manager.get_next_account(strategy="round_robin")

        if not account:
            raise HTTPException(status_code=503, detail="没有可用账号")

        try:
            response_generator = provider.chat_completion(request_data, account["token"])

            # 更新统计
            db_manager.update_stats(account["id"])
            duration = int((time.time() - start_time) * 1000)
            db_manager.add_log(account["name"], model, "SUCCESS", duration)
            
            return StreamingResponse(response_generator, media_type="text/event-stream")
        except Exception as e:
            logger.error(f"账号 {account['name']} 失败: {e}")
            db_manager.add_log(account["name"], model, "ERROR", int((time.time() - start_time) * 1000))
            continue
            
    raise HTTPException(status_code=503, detail="所有账号均调用失败")

@app.get("/v1/models")
async def list_models():
    """返回所有支持的模型列表"""
    models = [
        {"id": "gemini-3-pro-image-preview", "object": "model", "owned_by": "zai", "name": "Nano Banana Pro"},
        {"id": "gemini-2.5-pro", "object": "model", "owned_by": "zai", "name": "Gemini 2.5 Pro"},
        {"id": "claude-opus-4-20250514", "object": "model", "owned_by": "zai", "name": "Claude Opus 4"},
        {"id": "claude-sonnet-4-5-20250929", "object": "model", "owned_by": "zai", "name": "Claude Sonnet 4.5"},
        {"id": "claude-sonnet-4-20250514", "object": "model", "owned_by": "zai", "name": "Claude Sonnet 4"},
        {"id": "claude-haiku-4-5-20251001", "object": "model", "owned_by": "zai", "name": "Claude Haiku 4.5"},
        {"id": "o1-2024-12-17", "object": "model", "owned_by": "zai", "name": "o1"},
        {"id": "o3-pro-2025-06-10", "object": "model", "owned_by": "zai", "name": "o3-pro"},
        {"id": "grok-4-1-fast-reasoning", "object": "model", "owned_by": "zai", "name": "Grok 4.1 Fast"},
        {"id": "grok-4-0709", "object": "model", "owned_by": "zai", "name": "Grok 4"},
        {"id": "o4-mini-2025-04-16", "object": "model", "owned_by": "zai", "name": "o4-mini"},
        {"id": "gpt-5-2025-08-07", "object": "model", "owned_by": "zai", "name": "GPT-5"},
        {"id": "gemini-2.5-flash-image", "object": "model", "owned_by": "zai", "name": "Nano Banana"},
    ]
    return {"object": "list", "data": models}

# --- 刷新控制 ---
@app.post("/api/token/refresh/{account_id}")
async def refresh_token_api(account_id: int):
    success = await auto_refresh_service.refresh_token_now(account_id)
    if success:
        return JSONResponse({"success": True, "message": "刷新成功"})
    return JSONResponse(status_code=500, content={"success": False, "message": "刷新失败"})

@app.post("/api/settings/preview-mode")
async def set_preview_mode(request: Request):
    data = await request.json()
    auto_refresh_service.set_preview_mode(data.get("enabled", False))
    return JSONResponse({"success": True})

@app.post("/api/refresh/force")
async def force_refresh_all():
    """强制刷新所有浏览器账号"""
    accounts = db_manager.get_all_accounts(active_only=True)
    browser_accounts = [acc for acc in accounts if acc['token_source'] == 'browser']
    
    if not browser_accounts:
        return JSONResponse(status_code=400, content={
            "success": False,
            "message": "没有浏览器来源的账号"
        })
    
    # 异步刷新所有账号
    for account in browser_accounts:
        asyncio.create_task(auto_refresh_service.refresh_token_now(account['id']))
    
    return JSONResponse({
        "success": True,
        "message": f"已启动刷新任务，将依次刷新 {len(browser_accounts)} 个账号"
    })

@app.get("/api/account/status")
async def get_account_status():
    """获取所有账号的Token有效性状态"""
    accounts = db_manager.get_all_accounts()
    status_list = []

    for account in accounts:
        is_valid = provider.verify_token(account['token']) if account.get('token') else False
        status_list.append({
            "id": account['id'],
            "name": account['name'],
            "is_active": account['is_active'],
            "is_valid": is_valid,
            "total_calls": account['total_calls'],
            "token_source": account['token_source'],
            "expires_at": account.get('expires_at'),
            "data_dir": account.get('data_dir')
        })

    return JSONResponse({"accounts": status_list})


@app.get("/api/stats")
async def get_stats():
    """获取系统统计信息"""
    stats = db_manager.get_stats()
    return JSONResponse(stats)


# --- 健康检查 ---
@app.get("/health")
async def health_check():
    """
    健康检查端点 - 用于 Docker/K8s 探针

    返回:
        - status: 服务状态 (healthy/degraded/unhealthy)
        - uptime: 运行时间（秒）
        - accounts: 可用账号数
        - version: 版本号
    """
    stats = db_manager.get_stats()
    uptime = int(time.time() - _start_time)

    # 判断健康状态
    if stats["active_accounts"] > 0:
        status = "healthy"
    elif stats["total_accounts"] > 0:
        status = "degraded"
    else:
        status = "unhealthy"

    return JSONResponse({
        "status": status,
        "uptime": uptime,
        "version": settings.APP_VERSION,
        "accounts": {
            "active": stats["active_accounts"],
            "total": stats["total_accounts"],
        },
        "config": {
            "rate_limit_enabled": settings.RATE_LIMIT_ENABLED,
            "load_balance_strategy": settings.LOAD_BALANCE_STRATEGY,
        },
    })


@app.get("/health/live")
async def liveness_probe():
    """存活探针 - 检查服务是否运行"""
    return JSONResponse({"status": "alive"})


@app.get("/health/ready")
async def readiness_probe():
    """就绪探针 - 检查服务是否可以接收请求"""
    stats = db_manager.get_stats()
    if stats["active_accounts"] > 0:
        return JSONResponse({"status": "ready"})
    return JSONResponse({"status": "not_ready"}, status_code=503)

# --- 辅助函数 ---
@app.post("/api/service/stop")
async def stop_service():
    """停止服务"""
    logger.warning("🛑 收到停止服务请求")
    
    def shutdown():
        import os, signal
        os.kill(os.getpid(), signal.SIGTERM)
    
    # 3秒后停止
    asyncio.get_event_loop().call_later(3, shutdown)
    
    return JSONResponse({
        "success": True,
        "message": "服务将在3秒后停止..."
    })

async def perform_breakpoint_update():
    """启动时检查过期 Token"""
    try:
        accounts = db_manager.get_all_accounts(active_only=True)
        browser_accounts = [acc for acc in accounts if acc['token_source'] == 'browser']
        
        if not browser_accounts:
            logger.info("ℹ️ 没有浏览器账号需要检查")
            return
        
        logger.info(f"📊 检查 {len(browser_accounts)} 个浏览器账号...")
        
        for acc in browser_accounts:
            if acc.get('expires_at'):
                try:
                    exp = datetime.fromisoformat(acc['expires_at'])
                    remaining = (exp - datetime.now()).total_seconds()
                    
                    if remaining < 3600:
                        logger.warning(f"⚠️ 账号 [{acc['name']}] 即将过期（{int(remaining/60)}分钟后），开始刷新...")
                        await auto_refresh_service.refresh_token_now(acc['id'])
                    else:
                        logger.info(f"✅ 账号 [{acc['name']}] Token有效（{int(remaining/3600)}小时后过期）")
                except Exception as e:
                    logger.error(f"检查账号 [{acc['name']}] 失败: {e}")
    except Exception as e:
        logger.error(f"断点更新失败: {e}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=settings.PORT)
