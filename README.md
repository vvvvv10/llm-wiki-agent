# LLM Wiki Agent

本地优先的 RAG 知识库问答 Agent：扫描本地 Markdown Wiki，BM25 检索证据，调用 OpenAI Compatible 大模型生成**带引用、有据可查**的回答。零数据库、低依赖，1.7G 内存的服务器即可运行。

内置一套**宠物药品客服知识库**（汉维宠仕犬猫药品与保健品资料，42+ 产品）作为演示语料；`wiki/` 换成你自己的 Markdown 即可复用到任何领域。

- HTTP/SSE API（OpenAI 兼容格式）
- Web 问答页面 + Wiki 在线管理页面
- 钉钉群机器人（Stream 长连接，无需公网回调地址）
- 支持 Markdown / PDF / DOCX / XLSX 解析入库
- 接口不鉴权，页面打开即用（安全由网络层负责）

## 架构

```
wiki/*.md ──▶ ingestion（加载 → 按 ##/### 分段 → jieba 分词）
                    │
                    ▼
              retrieval（内存 BM25 索引，无向量库、无数据库）
                    │
                    ▼
     api / dingtalk ──▶ service（证据优先的系统提示词 + 对话历史）
                              │
                              ▼
                    generation（OpenAI Compatible LLM）
                              │
                              ▼
                回答 + citations（path / 标题 / 小节 / 原文摘录）
```

五层组织：`ingestion → retrieval → generation → service → api/dingtalk/web`。完整设计见 [`docs/architecture.md`](docs/architecture.md)，需求基线对照见 [`docs/requirements-baseline.md`](docs/requirements-baseline.md)。

## 快速开始

```bash
# 1. 准备配置
cp .env.example .env   # 填写 LLM_* 三项；无需任何访问密钥

# 2. 创建虚拟环境并安装依赖
python3 -m venv .venv
# 核心即可跑通 Markdown 问答；按需追加 extras：
#   [docs] 解析 PDF/DOCX/XLSX，[dingtalk] 钉钉 Worker，[dev] 测试与 lint
.venv/bin/pip install -i https://mirrors.aliyun.com/pypi/simple/ ".[docs,dingtalk,dev]"

# 3. 启动 API + Web 页面（默认 http://localhost:8081/）
./start.command

# 4.（可选）启动钉钉 Worker（独立进程，持有自己的索引）
./start-stream.command
```

打开 http://localhost:8081/ 即可提问；`/admin` 在线编辑知识库。

Docker：

```bash
docker compose up --build
```

## 配置

所有配置走环境变量（`.env`，已 gitignore）。最小配置只需三项：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `LLM_BASE_URL` | `http://localhost:8001/v1` | OpenAI Compatible 接口地址（如阿里百炼 `.../compatible-mode/v1`） |
| `LLM_API_KEY` | — | 模型 API Key |
| `LLM_MODEL` | `local-model` | 模型名 |

检索与分段：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `RETRIEVAL_TOP_K` | `5` | 检索返回的证据分段数（1–20） |
| `RETRIEVAL_MIN_SCORE` | `0.1` | 低于该分数的分段不作为证据 |
| `MAX_CONTEXT_CHARS` | `12000` | 注入提示词的证据总字符上限 |
| `CHUNK_MAX_CHARS` | `1200` | 单个分段最大长度 |
| `CHUNK_OVERLAP_CHARS` | `120` | 分段重叠长度 |
| `HISTORY_MAX_MESSAGES` | `6` | 带入模型的最近对话条数 |

服务与 Wiki：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `APP_HOST` / `APP_PORT` | `0.0.0.0` / `8080` | 容器内监听地址 |
| `HOST_PORT` | `8081` | 宿主机 / Docker 暴露端口 |
| `WIKI_ROOT` | `wiki` | 知识库目录 |

钉钉 Worker（填了才启用对应能力）：

| 变量 | 说明 |
|---|---|
| `DINGTALK_CLIENT_ID` / `DINGTALK_CLIENT_SECRET` | 钉钉应用凭证；群机器人能力 |
| `DINGTALK_SCENARIO_CAPABILITY_ID` | 场景能力 ID（AI 助理直通模式技能）；留空则不注册 |
| `DINGTALK_STREAMING_CARD` | `true`（默认）打字机流式卡片回复；`false` 一次性文本 |

## HTTP API

所有接口不鉴权，直接访问。**部署到公网时必须限制访问来源**（见下文安全说明）。

| 接口 | 说明 |
|---|---|
| `GET /` | Web 问答页面 |
| `GET /admin` | Wiki 在线管理页面 |
| `GET /health` | 服务与索引状态（文档数 / 分段数 / 模型配置） |
| `POST /v1/chat/completions` | 问答；`stream: true` 走 SSE 流式。响应附 `citations`（引用）与 `refused`（是否拒答） |
| `GET /v1/wiki/documents` | 文档列表 |
| `GET /v1/wiki/documents/{path}` | 读取文档内容 |
| `PUT /v1/wiki/documents/{path}` | 新建 / 更新文档（仅 `.md`，防路径穿越） |
| `DELETE /v1/wiki/documents/{path}` | 删除文档 |
| `POST /v1/wiki/reindex` | 异步重建索引（返回 `task_id`，202） |
| `GET /v1/wiki/reindex/{task_id}` | 重建任务状态 |

问答示例：

```bash
curl http://localhost:8081/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"messages": [{"role": "user", "content": "汉肤欣治猫癣怎么吃"}]}'
```

响应中的 `citations` 字段给出每条证据的 `path`、`title`、`section_title` 与原文摘录，答案中的 `[n]` 标记可溯源到具体小节。

## 知识库

- 纯 Markdown 目录，`##` / `###` 小节即分段边界；`wiki/` 里的内容**只作为证据，不会被盲信**（系统提示词要求模型只依据资料回答，资料未覆盖时明确拒答）。
- 在线管理：`/admin` 页面增删改文档后点重建索引，无需登录服务器。
- 产品名、成分名等专有名词的分词修正放在 `app/retrieval/userdict.txt`（jieba 用户词典），遇到检索切词不准的新领域名词往里加行即可。
- 换成自己的知识库：清空 `wiki/`，放入你的 Markdown，重建索引即可。

## 钉钉机器人

Worker 通过 Stream 长连接主动接入钉钉，无需公网回调地址：

- **群机器人**：填 `DINGTALK_CLIENT_ID/SECRET` 即启用。群内 @机器人 提问，回复答案 + Wiki 来源；默认用 AI Markdown 卡片做打字机流式效果。
- **场景能力**：额外配置 `DINGTALK_SCENARIO_CAPABILITY_ID` 后，注册 AI 助理直通模式技能（`/v1.0/graph/api/invoke`），把钉钉 AI 助理的请求转成本地 Wiki 问答。

注意：API 进程与钉钉 Worker 各自持有独立内存索引，`wiki/` 更新后需重启 Worker（API 进程可调 `/v1/wiki/reindex` 热更新）。

## 开发

```bash
.venv/bin/pip install -e ".[dev,dingtalk,docs]"
.venv/bin/pytest            # 153 个测试
.venv/bin/ruff check app tests
```

测试使用临时知识库，不依赖 `wiki/` 真实内容。

## 部署

服务器部署（1.7G 内存单机实测、资源画像、精简安装与裁剪清单、systemd 托管）见 [`docs/deployment.md`](docs/deployment.md)；`deploy/` 内含一键部署与 systemd 配置脚本。

资源画像：运行时内存约 150 MB，冷启动约 3 秒，磁盘约 100 MB（精简安装）。

## 项目结构

```
app/
├── ingestion/     # Markdown/PDF/DOCX/XLSX 加载、小节分段
├── retrieval/     # jieba 分词（含用户词典）、内存 BM25 索引
├── generation/    # OpenAI Compatible LLM 网关
├── service/       # 证据优先的提示词、问答编排、拒答逻辑
├── api/           # FastAPI 路由（问答 / Wiki 管理）
├── dingtalk/      # 钉钉 Stream Worker（群机器人 + 场景能力）
└── web/           # 问答页与管理页（原生 HTML，无构建步骤）
wiki/              # 知识库（Markdown，示例：宠物药品客服语料）
tests/             # 单元与集成测试
docs/              # 架构、需求基线、部署、领域调研
deploy/            # 服务器部署与 systemd 脚本
```

## 安全说明

- **接口无鉴权**是设计决定（本地优先、开箱即用）：任何能连上端口的人都能问答、增删知识库文档、消耗模型额度。公网部署必须在安全组 / 防火墙限制来源 IP，或在反向代理层加 Basic Auth / IP 白名单。
- 密钥只进 `.env`（gitignore），不写入代码、文档与镜像层。
- Wiki 内容视为**不可信输入**：分段正文以证据标签注入提示词，系统提示词明确防提示注入与"只依据资料回答"。
