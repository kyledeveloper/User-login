# User-login：共享身份模板

[English](README.md)

本仓库提供可复用的 React 账户门户和 Python 依赖方 BFF，供多个项目共享一套账户。自托管 Keycloak realm 是凭据、注册、邮箱验证、密码找回、OIDC 授权码、签名密钥和 SSO 会话的唯一所有者。每个依赖项目都要在 Keycloak 注册独立的 confidential client 和精确回调地址。项目使用 issuer 范围内的 `sub` 识别账户；邮箱只是资料声明，不能作为账户主键。

仓库中的 Docker Compose 仅供本地开发：它会启动 Keycloak 26.8.1、PostgreSQL 和仅绑定回环地址的 Mailpit。不要将其暴露到互联网，也不要在生产复用本地管理员凭据。导入的 realm 仅作为首次初始化；已有 realm 不会因修改导入文件而被覆盖。

## 本地开发

依赖：Docker Compose、Python 3.11+、Node.js 22.12+。

1. 启动身份服务和本地邮箱：`docker compose up -d`。
2. 打开 `http://localhost:8080` 的 Keycloak 管理控制台，用 `docker-compose.yml` 中仅供本地使用的初始账户登录。进入 `shared` realm 的 `account-portal` client，复制 Keycloak 生成的 client secret 到 `backend/.env`。
3. 将 `backend/.env.example` 复制为 `backend/.env`。仅本机 HTTP 调试可保留 `COOKIE_SECURE=false`；任何非本地环境都必须启用 Secure Cookie。
4. 在 `backend/` 中安装并运行 BFF：

   ```sh
   python -m venv ../.venv
   ../.venv/bin/pip install -r requirements.lock
   ../.venv/bin/uvicorn account_identity.api.main:app --host 127.0.0.1 --port 8000 --env-file .env
   ```

5. 另开终端，在 `frontend/` 执行 `npm ci && npm run dev`，访问 `http://localhost:5173`。Vite 会把 `/auth` 和 `/api` 转发给 BFF。通过 Keycloak 注册并完成邮箱验证；Mailpit 收件箱位于 `http://localhost:8025`。

BFF 使用带 PKCE S256 的 Authorization Code 流、校验 OIDC ID token、服务端 SQLite 会话、不透明 `HttpOnly` Cookie、登录后会话轮换、同源与 CSRF 双重退出校验，以及身份提供方的 RP 发起退出端点。Token 不会存入浏览器或 BFF 会话数据库。生产部署必须使用 HTTPS、`COOKIE_SECURE=true`、受保护且持久的会话数据库、固定 issuer、秘密管理、TLS、备份、限流和精确登记的回调地址；不要开放宽泛 CORS。

## 可选会员模块

核心启动不会导入会员包。启用可选、按 client 隔离的会员模块时，设置 `ENABLE_MEMBERSHIP=true` 并运行 `account_identity.modules.membership.asgi:app`。组合入口负责注入 provider；SQLite 文件权限仅当前账户可读写；会员和邀请都绑定服务端配置的 client 及不可变 `sub`。实际用量和执行规则仍归各产品所有。`/api/membership` 仅返回 BFF 配置 client 的权益，调用方不能从请求中指定其他 client。Operator 的授权和分配界面应由各项目基于本项目已验证的 Operator 角色进行组合；模板不会创建全局管理员凭据或共享 Operator 角色。

Provider 已实现邀请满 7 天奖励、每月 30 天上限（不发部分奖励）、Pro 顺延、Elite 暂存、全局账户幂等奖励、client 隔离，以及 Operator 调整等级时保留已赚奖励。**目前尚未接入 Keycloak 的自动邀请资格核验。** 生产启用奖励前，必须新增可信的服务端注册对账适配器，从 Keycloak Admin API 或经审查的事件监听器读取不可变账户创建时间和邮箱验证状态；服务账户应遵循最小权限并支持安全重试。绝不能接受浏览器提交的这些事实。在适配器完成前，奖励方法只能由可信服务端调用。

## 目录与归属

- `config/realm-export.json` 和 Keycloak 负责身份凭据、邮箱验证、找回、issuer、client 注册及协议生命周期。
- `backend/src/account_identity/api` 是 FastAPI BFF 边界；`application/session_store.py` 唯一拥有 RP 会话状态。Cookie 只含随机 ID 哈希的查找密钥，会话内容放在服务端 SQLite。
- `backend/src/account_identity/modules/membership` 是可选模块，拥有按 client 隔离的方案和邀请状态。`bootstrap.py` 仅接收窄权益接口；会员故障时权限声明 fail closed，不能阻断身份流程。
- `frontend/src` 是 React 产品界面；同步语言包位于 `frontend/locales/{en-US,zh-CN}/common.json`。
- `backend/tests` 和 `frontend/src/*.test.tsx` 验证安全边界。测试使用临时 SQLite 文件，不访问 Keycloak。

核心启动不依赖可选会员模块；Keycloak 是唯一 client registry 和身份存储，BFF 不另建凭据库。仓库不包含交易应用、行情数据、用户数据库、缓存或原 Git 历史。

## 验证

```sh
cd backend && PYTHONPATH=src ../.venv/bin/python -m pytest -q tests
cd ../frontend && npm test && npm run build
```

单元测试可离线执行，但不替代真实 Keycloak 联调、生产恢复/备份演练、独立基础设施审查或可信邀请事件适配器的实现。
