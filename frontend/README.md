<!--
  前端项目说明文档
  记录启动方式、代理配置和部署时的环境变量
-->

# Enterprise Data & Policy Knowledge Workbench Frontend

企业数据分析与制度知识问答工作台前端，技术栈为 React + Vite + Tailwind CSS + pnpm。当前页面提供登录/注册、会话历史、统一 Agent 入口、SSE 流程展示和数据结果表格。

后端设置 `APP_ALLOW_SELF_REGISTRATION=true` 时，登录页会提供“注册账号”入口。新注册账号固定为普通用户，不能访问管理员页面；共享或公网部署应在接入邀请、邮箱验证、限流或 SSO 前关闭该开关。

## 启动

```bash
cd frontend
pnpm install
pnpm dev
```

默认开发代理会把 `/api` 转发到 `http://127.0.0.1:8000`，对应后端的 `/api/workbench` 工作台接口。

如需修改后端地址：

```bash
cp .env.example .env
```

然后调整：

```bash
VITE_DEV_PROXY_TARGET=http://127.0.0.1:8000
```

如果前端与后端不在同一域部署，可设置：

```bash
VITE_API_BASE_URL=http://127.0.0.1:8000
```

## 浏览器端到端测试

先启动完整 Docker 服务或本地前后端服务，再向当前 PowerShell 注入一个本地管理员账号。账号信息不会写入测试源码：

```powershell
$env:E2E_ADMIN_EMAIL = "你的管理员邮箱"
$env:E2E_ADMIN_PASSWORD = "你的管理员密码"
$env:E2E_BASE_URL = "http://127.0.0.1:5173"
$env:E2E_CHROME_PATH = "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
pnpm run test:e2e
```

测试使用 Playwright 覆盖用户自助注册、管理员登录、文档上传与索引、审计记录展示、普通用户 `403` 权限拒绝、文档隔离和测试数据清理。若机器没有已安装的 Chrome，可在网络代理可用时执行 `pnpm exec playwright install chromium` 安装测试浏览器。
