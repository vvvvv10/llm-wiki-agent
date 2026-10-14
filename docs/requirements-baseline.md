# LLM Wiki Agent 需求与技术基线（本地实现对照）

> 文档版本：1.0（依据用户提供的《LLM Wiki Agent 需求与技术设计文档 v1.0》，基线日期 2026-10-13）
> 本机项目目录：`/Users/qianjiajie/Desktop/llm-wiki-agent`
> 本文记录需求落地情况与本机实现的差异点。完整架构说明见 `docs/architecture.md`。

## 1. 已实现能力（与需求文档逐项对应）

| 需求 | 状态 | 对应实现 |
|---|---|---|
| BR-001/FR-001~006 本地 Wiki 加载与分段 | 已实现 | `app/ingestion/loader.py`、`app/ingestion/chunker.py`（H1~H4 分段、1200 字符上限、120 重叠、SHA-256 稳定 ID） |
| FR-007~009 BM25 中英文检索 | 已实现 | `app/retrieval/bm25.py`、`app/retrieval/tokenizer.py`（jieba + 停用词过滤，`top_k` 1~20，`min_score` 阈值） |
| FR-010/011 索引快照与重建 | 已实现 | `app/retrieval/index_store.py`（原子替换、非阻塞锁、并发 409） |
| FR-012~017 证据约束与拒答 | 已实现 | `app/service/answer_service.py`、`app/service/prompts.py`（无证据不调用模型、证据 ≤12000 字符、历史 ≤6 条、固定安全系统 Prompt） |
| FR-018~021 Chat Completions API | 已实现 | `app/api/chat.py`（非流式 + SSE、`wiki.citations`/`wiki.error` 事件、`X-Request-Id`） |
| FR-022~027 健康检查与管理 API | 已实现 | `app/main.py`、`app/api/wiki.py`（接口不鉴权，按用户决定移除 App/Admin Key 方案） |
| FR-028~030 Web 页面 | 已实现 | `app/web/index.html`（Fetch+SSE、引用卡片、textContent 渲染、Key 仅存 localStorage） |
| FR-031~034 钉钉 Stream 接入 | 已实现 | `app/dingtalk/runner.py`、`app/dingtalk/handler.py`（长连接、群消息问答、来源回复、异常写 `stream.log`） |
| FR-035 场景能力 ID 参与业务调用 | 已实现 | 配置了 `DINGTALK_SCENARIO_CAPABILITY_ID` 时，Runner 通过 `app/dingtalk/skill.py` 注册钉钉 AI 助理直通模式技能（`GraphMessage.TOPIC`），ID 注入处理器、写入日志并随响应元数据返回；留空则仅注册群机器人 |
| FR-036 钉钉逐 Token 流式回复 | 已实现 | `app/dingtalk/handler.py` 的 `run_streaming_card`：群内用官方 `AIMarkdownCardInstance` 打字机卡片逐 token 更新（`DINGTALK_STREAMING_CARD`，默认开），结束附引用来源，卡片创建失败自动回退纯文本 |
| FR-037 Wiki 在线管理页面 | 已实现 | `app/web/admin.html`（文档列表/新建/编辑/删除、重建索引进度轮询，打开即用无需登录）+ `app/api/wiki.py` 读写接口（路径穿越防护、文本编辑仅 `.md`） |
| 多格式文档解析（新增能力，超出基线） | 已实现 | `app/ingestion/parsers.py`：PDF/DOCX/XLSX → Markdown 后入库，`.[docs]` extra 可选安装；文件放入 `wiki/` 后 reindex 即生效，损坏文件单个跳过不阻断 |

## 2. 本机实现与需求文档的差异

1. **项目路径**：需求文档中的 `/Users/bob/Desktop/llm-wiki-agent` 在本机不存在；本实现在 `/Users/qianjiajie/Desktop/llm-wiki-agent` 从零搭建，启动脚本使用 `$(dirname "$0")`，与路径解耦。
2. **测试数量**：本机测试为 131 个（pytest 全部通过），覆盖 ingestion / retrieval / generation / service / api / dingtalk / e2e / config，多于文档中记录的 43 个。
3. **BM25 相对分数**：`RETRIEVAL_MIN_SCORE` 的相对相关度按“理论最高可达 BM25 分”归一化到 [0,1]（而非按本次查询最高分归一化），使阈值真正可过滤弱匹配。
4. **停用词**：tokenizer 增加了中英文常见停用词过滤，避免“的/了/是”等虚词造成弱匹配噪声。
5. **AnswerService 与索引生命周期**：AnswerService 持有 `IndexStore` 并在每次查询时读取最新快照，因此 API 进程内重建索引后问答立即生效（文档 7.1 描述的仅限钉钉 Worker 需重启一条仍然成立：Worker 与 API 是独立进程、独立索引）。
6. **模型配置**：`.env` 中模型地址/Key/模型名（默认 `3.8-max`）待用户填入真实值；未配置时聊天接口返回清洗后的 502，`/health` 的 `model_configured=false`。

## 3. 已知差距（对应文档第 22 节）

- GAP-001 ~~场景能力 ID 未参与钉钉调用~~ **已闭环**：场景能力通过钉钉 AI 助理直通模式技能（`GraphMessage.TOPIC`）进入业务调用链路，见 `app/dingtalk/skill.py`。
- GAP-002 ~~钉钉群回复非增量流式~~ **已闭环**：群内改用 AI Markdown 打字机卡片（`AIMarkdownCardInstance`）逐 token 更新，见 `app/dingtalk/handler.py` 的 `run_streaming_card`；开关 `DINGTALK_STREAMING_CARD`（默认开），卡片失败自动回退纯文本。
- GAP-003 API 与 Worker 索引不共享（P1）
- GAP-005 仅 BM25，语义召回弱（P1）
- GAP-006 引用为“提供给模型的证据”而非逐条校验（P1）
- GAP-007/008 无指标、无限流（P1）

## 4. 本机启动

```bash
cd /Users/qianjiajie/Desktop/llm-wiki-agent
./start.command          # API + Web：http://localhost:8081/
./start-stream.command   # 钉钉 Worker（需在 .env 填写 Client ID/Secret）
```

按用户决定，HTTP 接口不做访问鉴权（原 App/Admin Key 方案已移除），`.env` 只保存模型与钉钉凭证（`.env` 被 Git 忽略）。用户曾在交互中提供过的模型 API Key 与钉钉 Client Secret 不写入任何文档，使用前请在对应平台轮换并只写入本机 `.env`。部署到公网时必须在网络层（防火墙/安全组/反向代理）限制访问来源。
