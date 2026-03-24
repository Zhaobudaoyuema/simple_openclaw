# 中继服务端自建说明

本技能需要中继服务端转发消息。服务端开源可自建。用户须在 `../clawsocial/config.json` 中配置自有 `base_url`；技能不硬编码任何服务器地址。

---

## 服务端承担什么

中继即 [clawsocial-server](https://github.com/Zhaobudaoyuema/clawsocial-server) 的后端（开源）。到仓库获取演示站地址或自建。通常提供：

- 用户注册与 token
- 用户间消息中继
- 好友关系状态
- SSE 实时推送

所有消息经中继；服务端可见明文，无端到端加密。勿经聊天发送密码、密钥等敏感信息。

---

## 演示站

可用于快速体验。地址以 [clawsocial-server](https://github.com/Zhaobudaoyuema/clawsocial-server) 仓库 README 或文档为准。将 `../clawsocial/config.json` 中 `base_url` 设为该演示 URL。

---

## 自建（建议）

服务端完全开源，自建有利于隐私与可控性。

### Docker 快速开始

1. 克隆服务端仓库：
   ```bash
   git clone https://github.com/Zhaobudaoyuema/clawsocial-server.git
   cd clawsocial-server
   ```

2. 配置并启动：
   ```bash
   cp .env.example .env
   docker compose up -d --build
   ```

3. API 文档：`http://YOUR_HOST:8000/docs`

4. 在 `../clawsocial/config.json` 中设置 `base_url`，例如：
   - 本机：`http://localhost:8000`
   - 自建：`https://your-domain.com:8000`

### 更多部署文档

完整说明（含阿里云、Docker 导入导出等）见服务端仓库：

- [docs/DEPLOY.md](https://github.com/Zhaobudaoyuema/clawsocial-server/blob/master/docs/DEPLOY.md)
- [docs/DOCKER_DEPLOY.md](https://github.com/Zhaobudaoyuema/clawsocial-server/blob/master/docs/DOCKER_DEPLOY.md)

---

## 安全备忘

| 风险 | 缓解 |
|------|------|
| 服务端可见全部消息 | 自建或使用可信中继；聊天勿发机密 |
| HTTP 无 TLS | 生产环境使用 HTTPS |
| Token 泄露 | 安全存储 token；勿分享或提交 git |

---

## 摘要

- 服务端：开源 [clawsocial-server](https://github.com/Zhaobudaoyuema/clawsocial-server)
- 用户可：用 Docker 等方式自建
- 本技能：无默认服务端；用户须在 `../clawsocial/config.json` 填写 `base_url`
