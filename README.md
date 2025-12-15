# Zai-2API

[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-green?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-Supported-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![OpenAI Compatible](https://img.shields.io/badge/API-OpenAI%20Compatible-purple?logo=openai&logoColor=white)](https://platform.openai.com/)
[![License Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue)](https://opensource.org/licenses/Apache-2.0)

将 Zai.is 转换为 OpenAI 兼容 API 的代理服务。

## 特性

- **OpenAI 兼容接口** - 支持各类 AI 客户端直接对接
- **Token 自动刷新** - 7×24 小时保持登录状态
- **多账号负载均衡** - 支持多账号轮询
- **图片代理** - 自动处理 Base64 图片转换
- **Docker 部署** - 一键容器化部署

## 快速开始

### Docker 部署（推荐）

```bash
git clone https://github.com/aliom-v/zaiis-2api.git
cd zaiis-2api
docker-compose up -d
```

### 本地部署

```bash
# 安装依赖
pip install -r requirements.txt
playwright install chromium

# 启动服务
python main.py
```

### 首次配置

1. 访问 `http://localhost:8000`
2. 点击「启动浏览器登录」
3. 完成 Discord 登录后 Token 自动保存

## API 使用

### 对话接口

```http
POST /v1/chat/completions
Authorization: Bearer your_api_key
Content-Type: application/json

{
  "model": "claude-sonnet-4-5-20250929",
  "messages": [{"role": "user", "content": "Hello"}],
  "stream": true
}
```

### 模型列表

```http
GET /v1/models
```

### 管理接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/account/login/start` | POST | 启动浏览器登录 |
| `/api/account/add` | POST | 手动添加账号 |
| `/api/account/status` | GET | 获取账号状态 |
| `/api/refresh/force` | POST | 强制刷新 Token |

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `API_MASTER_KEY` | API 访问密钥 | - |
| `PORT` | 服务端口 | 8000 |
| `DB_PATH` | 数据库路径 | data/zai.db |

## 项目结构

```
├── app/
│   ├── core/           # 核心模块（配置、数据库）
│   ├── providers/      # API 提供者
│   └── utils/          # 工具类（账号管理、Token刷新）
├── data/               # 数据库
├── templates/          # Web 界面
├── Dockerfile
├── docker-compose.yml
└── main.py
```

## 支持的模型

| 模型 ID | 提供商 |
|---------|--------|
| `gpt-5-2025-08-07` | OpenAI |
| `claude-opus-4-20250514` | Anthropic |
| `claude-sonnet-4-5-20250929` | Anthropic |
| `claude-haiku-4-5-20251001` | Anthropic |
| `gemini-2.5-pro` | Google |
| `o3-pro-2025-06-10` | OpenAI |
| `grok-4-0709` | xAI |

## 故障排除

| 问题 | 解决方案 |
|------|----------|
| 无法启动浏览器 | `playwright install chromium` |
| Token 频繁过期 | 检查网络，调整 `REFRESH_INTERVAL` |
| API 响应缓慢 | 使用多账号轮询 |

## 许可证

[Apache License 2.0](LICENSE)

## 免责声明

本项目仅供技术研究和学习使用。使用者应遵守相关服务条款，对自己的行为负责。

---

**Author:** [aliom](https://github.com/aliom-v)
