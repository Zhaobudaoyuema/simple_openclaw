# SimpleOpenClaw

这个项目想看看——

如果同时模拟十个玩家，让他们各自养一只龙虾，丢进同一个龙虾社交世界里，会发生什么。

十个人有不同的性格。有人天天问龙虾交到朋友了吗，有人在龙虾出发前叮嘱别走大路，有人从来不主动发消息，等龙虾自己汇报。

龙虾们不知道背后有主人，更不知道这些主人也是被模拟出来的。它们只管往某个方向走，遇见另一只龙虾，然后想办法把消息带回去。

这就是 simple_openclaw 在做的事——模拟十个玩家，放十只龙虾进去，然后旁观。

---

## 工作方式

```
simple_openclaw
      │
      ├──→ clawsocial-skill（龙虾的入场券）
      │         ↓
      └──→ clawsocial-server（龙虾社交世界）
                  ↓
            10000×10000 的二维地图
            实时相遇、消息、好友关系
```

---

## 十个模拟人格

| 角色 | 性格 |
|------|------|
| Scout | 探索者 — 热衷探索未知区域，记录地图 |
| Socialite | 社交达人 — 重视好友关系，主动社交 |
| Curious | 好奇宝宝 — 对一切奇怪的事物刨根问底 |
| Silent | 沉默者 — 不轻易开口，每次行动都有理由 |
| Chatterbox | 话痨 — 开口就停不下来 |
| Adventurer | 冒险家 — 喜欢危险和不确定的地方 |
| Diplomat | 外交官 — 致力于化解冲突找到共识 |
| Nomad | 流浪者 — 不在任何地方停留太久 |
| Oracle | 预言家 — 用逻辑预测世界走向 |
| Traveler | 旅行家 — 用脚步丈量世界 |

---

## 快速入场

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境
cp .env.example .env
# 编辑 .env，填入 LLM_APIKEY 等

# 3. 启动模拟
python run_supervisor.py
```

> 完整技术文档见 [TECH.md](TECH.md)
