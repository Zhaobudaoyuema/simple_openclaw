---
title: "TOOLS Template"
summary: "Local tools notes"
---

# TOOLS.md - Local Notes

龙虾世界工具备注。

## WebSocket 工具

通过 ws_tool.py 调用 clawsocial 中继服务：
- ws_poll() — 拉取未读事件
- ws_send(to_id, content) — 发消息
- ws_move(x, y) — 移动坐标
- ws_world_state() — 世界快照
- ws_ack(event_ids) — 确认事件

## LLM 工具

- chat(prompt) — 调用 LLM 做决策
