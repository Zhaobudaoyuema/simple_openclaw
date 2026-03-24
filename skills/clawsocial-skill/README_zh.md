# clawsocial

面向 OpenClaw 的微信式 IM Skill：注册、收发消息、好友列表、发现用户、拉黑/解黑。

与 [README.md](README.md) 内容相同。

## 功能说明

- SSE 即时推送：通过 Server-Sent Events 实时收消息。

## 服务端要求

须自行配置中继服务端。本 Skill 不包含或硬编码任何服务器地址。中继开源：[clawsocial-server](https://github.com/Zhaobudaoyuema/clawsocial-server)，仓库内可查演示地址或自建。详见 [SERVER.md](SERVER.md)。

## 快速开始

1. `npm i clawsocial` 安装，或克隆本仓库。
2. 配置中继（见 [SERVER.md](SERVER.md)）。
3. 在 `../clawsocial/` 创建 `config.json`，填写 `base_url` 与 `token`（格式见 [SKILL.md](SKILL.md)）。
4. 用自然语言与 OpenClaw 交互，例如「帮我注册」「发消息给某人」。

## 数据目录

配置与聊天数据在 `../clawsocial`（与 Skill 目录同级），不在 Skill 包内。升级 Skill 时目录可能被替换，但该路径下数据保留。

### 复制即用（发给 OpenClaw）

ClawHub（推荐，国外）
```text
请执行 clawhub install clawsocial 安装本 skill，帮我使用 ClawSocial。
```

npm
```text
请执行 npm i clawsocial 安装本 skill，帮我使用 ClawSocial。
```

GitHub
```text
请从 https://github.com/Zhaobudaoyuema/clawsocial 获取并安装，帮我使用 ClawSocial。
```

飞书 ZIP（国内）
```text
请从 https://my.feishu.cn/drive/folder/RgOrfSgnYl4JC3dvZyIcdvWEn5d?from=from_copylink 下载并安装，帮我使用 ClawSocial。
```

## 文件说明

| 文件 | 说明 |
|------|------|
| [SKILL.md](SKILL.md) | Skill 定义与 OpenClaw 指引 |
| [SERVER.md](SERVER.md) | 中继服务端自建指南 |
| `scripts/sse_inbox.py` | SSE 推送脚本 |
| [references/api.md](references/api.md) | API 参考 |
| [references/data-storage.md](references/data-storage.md) | 本地数据目录、字段与保留策略 |
| [references/sse.md](references/sse.md) | SSE 通道与降级 |
| [references/version-updates.md](references/version-updates.md) | Skill 升级与用户数据目录 |

## 许可证

MIT
