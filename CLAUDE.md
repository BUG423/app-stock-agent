# StockAgent 项目指南（面向 AI / 开发者）

> **完整项目说明见 [README.md](./README.md)**（架构、技术栈、部署、定时任务、关键文件、DB表、运维CLID、配置、踩坑点、代码风格）。
> 本文件只补充 AI 协作 / 维护时的要点与约定。
> 最后更新：2026-07-17

---

## 1. 一句话

自动跟踪三位 B 站真人博主（**跳跳虎复盘**、**妖板选手**、**提前炒股早退休**）→ 忠实提取原文标的与条件 → 邮件提醒 → 盘中盯盘。提前早退休的短实盘视频使用本地 RapidOCR，并以名称、持仓数字和发布时间可见行情交叉核验标的。系统不自动下单。

## 2. 当前范围（2026-07 精简后）

- **保留**：三位真人博主的 B 站抓取、原文/条件提取、本地 OCR、行情验股、盯盘、盘后复盘、邮件队列、健康巡检、AI 复核官和 Web 面板。
- **已删除**：AI 方法论蒸馏、AI 虚拟博主、自动筛选/选股、相关回测入口及前端策略页面。不要重新引入自动选股或自动交易。

## 3. 部署与协作约定

- 服务器 `ssh root` → `/root/workspace/stockagent`，SQLite，pm2 跑 `sa-scheduler` / `sa-web(:3003)`。详见 README §4。
- **GitHub `git@github.com:BUG423/stock-agent.git`，唯一分支 `master`**（2026-07 已把所有分支合并进 master 并删除其余）。改代码走 git：改 → commit → push → 另一处 pull → 重启对应进程。本仓库私有。
- 服务器改代码时可用 scp 传单文件后在服务器 `git add/commit/push`；重启前务必先 `python3 -c 'from stockagent.scheduler.scheduler import create_scheduler'` 验证导入，避免整个调度器崩。
- 提交前 `ruff format stockagent/ && ruff check stockagent/`（全绿）+ `python3 -m pytest tests/ -q`。

## 4. 用户硬要求 / 易错点

1. **只跟踪真人原文**：不从博主历史内容蒸馏策略，不生成 AI 候选。提前早退休仅在 OCR 名称片段、持仓数字和发布时行情唯一匹配时生成直接型提醒；跳跳虎、妖板按本条明确条件盯盘。任何信号都不自动下单。
2. **只认 A 股**：非 A 股(美股/港股)代码不进盯盘（`market/stock_client.is_a_share()`）。凯莱英 002821 是 A 股（曾被误认为美股，实为创新药涨停高价）。
3. **内容新鲜度与可靠性**：pipeline 只处理近 N 天 + 绝对下限之后的动态；范围内内容先落原始库，再过滤/分析，失败可重试。B站 scraper **不按 since 跳过**，新鲜度靠 `pipeline._filter_recent`。
4. **邮件**：To=主号(`user@example.com`)、其余 to_addresses 作 Cc 抄送(`cc@example.com`)；博主条件取精简版 `description`。
5. **表空 ≠ 故障**：数据曾被用户主动清零从 0 重建，别把空表当 bug 排查/造数。
6. **绝不编造工具返回 / 不臆造攻击事件**：历史上有过助手编造"注入删库"的教训，一切以真实命令输出为准。
7. **密钥**：`.env` 已 gitignored，实值在服务器 `.env`，模板 `.env.example`；勿把真实密钥写进代码或文档。

## 5. 记忆

跨会话记忆见 `~/.claude/projects/-home-mz-WebClaudeWorkspaces-Stock/memory/`（含本次转向、服务器定位、用户反馈等）。关键一条：[[stockagent-ai-assist-pivot]]。
