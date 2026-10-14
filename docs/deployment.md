# 部署指南（低资源配置）

目标环境：单机服务器（如 你的服务器IP），资源可能紧张。本文基于本机实测数据。

## 已部署实例（你的服务器IP）

| 项 | 值 |
|---|---|
| 代码目录 | `/opt/llm-wiki-agent` |
| Python | uv 安装的独立 CPython 3.11（系统自带 3.6 太老） |
| API 端口 | **8090**（8080-8086 均已被服务器已有应用/Docker 占用） |
| 服务方式 | systemd：`llm-wiki-agent`（API）、`llm-wiki-agent-worker`（钉钉） |
| swap | 1G `/swapfile`（内存仅 1.7G，已写入 fstab 开机自动挂载） |
| 访问地址 | http://你的服务器IP:8090（问答）、`/admin`（管理） |

常用命令（服务器上）：

```bash
systemctl status llm-wiki-agent llm-wiki-agent-worker   # 查看状态
systemctl restart llm-wiki-agent                        # 改 wiki/ 后重启（或调 /v1/wiki/reindex）
journalctl -u llm-wiki-agent-worker -f                 # 看钉钉 Worker 日志
```

**安全组务必限制 8090 端口来源 IP**——接口无鉴权，公网裸奔任何人都能改知识库、消耗模型额度。

## 资源画像（实测）

| 项目 | 数值 | 说明 |
|---|---|---|
| 运行时内存（RSS） | ~150 MB | 13 篇文档、86 分段、索引常驻内存 |
| 磁盘（精简安装） | ~100 MB | 仅核心运行时，不含 dev/docs |
| 磁盘（完整安装） | ~156 MB | 含 pytest、pypdf、openpyxl、python-docx |
| 冷启动 | ~3 秒 | 含 jieba 词典加载与索引构建 |
| Wiki 语料 | 52 KB | 13 篇 Markdown |

## 安装方式二选一

### 方式 A：裸机 venv（推荐，最省资源）

只装核心依赖，不装解析 extra（省约 32 MB 磁盘）：

```bash
python3.11 -m venv .venv
.venv/bin/pip install -i https://mirrors.aliyun.com/pypi/simple/ .
# 仅当需要钉钉 Worker 时：
.venv/bin/pip install -i https://mirrors.aliyun.com/pypi/simple/ ".[dingtalk]"
# 仅当需要在线解析 PDF/DOCX/XLSX 时：
.venv/bin/pip install -i https://mirrors.aliyun.com/pypi/simple/ ".[docs]"
```

启动：

```bash
cp .env.example .env   # 填入真实密钥
set -a; source .env; set +a
nohup .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8081 > api.log 2>&1 &
```

### 方式 B：Docker

Dockerfile 已按精简设计（`pip install .`，不含 dev/docs/dingtalk）：

```bash
docker compose up -d --build
```

`wiki/` 以只读卷挂载；改文档后 `POST /v1/wiki/reindex` 生效。

## 依赖体积分层

| 层 | 包 | 磁盘 | 说明 |
|---|---|---|---|
| 核心 | fastapi/uvicorn/httpx/pydantic/jieba/numpy/rank_bm25 等 | ~72 MB | 必装。其中 jieba 词典 38M、numpy 24M 是大头，无法裁剪 |
| 解析 extra | `.[docs]`：pypdf + python-docx + openpyxl | +32 MB | 大头是 python-docx 拖入的 lxml（19M）。不解析这些格式就别装 |
| 钉钉 extra | `.[dingtalk]`：dingtalk-stream + aiohttp | +8 MB | 仅 Worker 需要；API 进程不需要 |
| 开发 | `.[dev]`：pytest + ruff | +10 MB | 生产不装 |

## 资源不足时的裁剪清单

按收益排序：

1. **不装 `.[docs]`**：省 32 MB 磁盘。前提：服务器的 `wiki/` 目录里只有 `.md` 文件。注意 reindex 会重新解析源文件，若 `wiki/` 里有 PDF/XLSX 而解析库缺失，这些文件会被跳过（不报错、不阻断其他文档）。
2. **二进制文档转 Markdown 后再入库（推荐）**：在本地用管理页面或解析器把 PDF/XLSX/DOCX 转成 Markdown，只把 `.md` 放进服务器的 `wiki/`。解析库就只在本地开发机需要，服务器保持最精简安装。
3. **精简 wiki 语料**：文档目录是纯文本，规模可控；删除测试/示例文档可忽略不计。
4. **不 ship 测试与缓存**：`tests/`、`.pytest_cache/`、`.ruff_cache/` 不需要上传服务器（合计约 650 KB）。
5. **swap 兜底**：若服务器内存 < 512 MB，建议配置 512 MB swap，应对冷启动峰值（jieba 加载期瞬时内存高于稳态）。

## 注意事项

- **接口无鉴权**：按需求去掉了所有访问密钥，任何能连上端口的人都能问答、增删文档。部署到公网（如 你的服务器IP）时必须限制访问来源：安全组/防火墙只放行公司出口 IP，或在反向代理（nginx）层加 IP 白名单 / Basic Auth。
- `.env` 必须放在服务器上并 `chmod 600`；**不要**把 `.env` 提交到镜像或代码仓库（Dockerfile 只 `COPY` `pyproject/README/app/wiki`，`.env` 经 compose 的 `env_file` 注入）。
- API 与钉钉 Worker 是两个进程、各自持有内存索引；`wiki/` 更新后 Worker 需重启（API 可用 reindex 热更新）。
- 若服务器无法访问公网 PyPI，所有安装命令已使用阿里云镜像源。
- 生产使用前轮换所有曾出现在本机的凭证（钉钉 AppSecret、百炼 API Key、本项目 API Key）。
