"""Wiki 管理 API：文档列表、在线编辑、异步重建索引。

文档写接口的安全规则：
- 仅接受 ``.md`` 文件。
- 路径解析后必须仍位于 Wiki 根目录内（防目录穿越）。
- 拒绝隐藏目录与符号链接。
- 写操作后需调用 ``/v1/wiki/reindex`` 使新内容进入索引。
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from app.retrieval.index_store import RebuildInProgressError

logger = logging.getLogger(__name__)

router = APIRouter()

# 任务状态仅保存在内存中（已知差距 GAP-012：重启后丢失）。
_tasks: dict[str, dict] = {}

MAX_CONTENT_CHARS = 200_000


class DocumentPut(BaseModel):
    content: str


def _wiki_root(request: Request) -> Path:
    return Path(request.app.state.settings.wiki_root).expanduser().resolve()


def _resolve_doc_path(root: Path, raw_path: str) -> Path:
    """把请求里的相对路径解析成 Wiki 根目录内的真实文件路径。

    防护：仅 ``.md``、拒绝绝对路径与 ``..`` 穿越、拒绝隐藏目录与符号链接。
    """
    raw = (raw_path or "").strip().lstrip("/")
    if not raw or not raw.endswith(".md"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_PATH", "message": "仅支持 .md 文档路径。"},
        )
    candidate = (root / raw).resolve()
    if not candidate.is_relative_to(root):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "PATH_ESCAPE", "message": "路径超出 Wiki 根目录。"},
        )
    parts = candidate.relative_to(root).parts
    if any(part.startswith(".") for part in parts):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "HIDDEN_PATH", "message": "不允许访问隐藏目录或文件。"},
        )
    return candidate


@router.get("/v1/wiki/documents")
def list_documents(request: Request):
    snapshot = request.app.state.index_store.snapshot
    items = []
    if snapshot is not None:
        chunk_counts: dict[str, int] = {}
        for chunk in snapshot.chunks:
            chunk_counts[chunk.document_path] = chunk_counts.get(chunk.document_path, 0) + 1
        for document in sorted(snapshot.documents, key=lambda d: d.path):
            items.append(
                {
                    "path": document.path,
                    "title": document.title,
                    "summary": document.content[:160],
                    "chunks": chunk_counts.get(document.path, 0),
                }
            )
    return {"items": items}


@router.get("/v1/wiki/documents/{doc_path:path}")
def get_document(doc_path: str, request: Request):
    root = _wiki_root(request)
    path = _resolve_doc_path(root, doc_path)
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "文档不存在。"},
        )
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "READ_FAILED", "message": "文档读取失败。"},
        )
    return {"path": doc_path.strip("/"), "content": content}


@router.put("/v1/wiki/documents/{doc_path:path}", status_code=201)
def put_document(doc_path: str, payload: DocumentPut, request: Request):
    if len(payload.content) > MAX_CONTENT_CHARS:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"code": "CONTENT_TOO_LARGE", "message": "文档内容过长。"},
        )
    root = _wiki_root(request)
    path = _resolve_doc_path(root, doc_path)
    created = not path.is_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload.content, encoding="utf-8")
    except OSError:
        logger.exception("failed to write wiki document: %s", path)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "WRITE_FAILED", "message": "文档保存失败。"},
        )
    return {
        "path": doc_path.strip("/"),
        "created": created,
        "reindex_required": True,
    }


@router.delete("/v1/wiki/documents/{doc_path:path}", status_code=200)
def delete_document(doc_path: str, request: Request):
    root = _wiki_root(request)
    path = _resolve_doc_path(root, doc_path)
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "文档不存在。"},
        )
    try:
        path.unlink()
    except OSError:
        logger.exception("failed to delete wiki document: %s", path)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "DELETE_FAILED", "message": "文档删除失败。"},
        )
    return {"path": doc_path.strip("/"), "deleted": True, "reindex_required": True}


@router.post("/v1/wiki/reindex", status_code=202)
def reindex(request: Request):
    store = request.app.state.index_store
    task_id = str(uuid.uuid4())
    _tasks[task_id] = {"task_id": task_id, "status": "RUNNING"}

    def _finished(version: str | None) -> None:
        if version:
            _tasks[task_id].update(status="SUCCEEDED", index_version=version)
        else:
            _tasks[task_id].update(status="FAILED", error_code="INDEX_BUILD_FAILED")

    try:
        thread = store.try_rebuild_background(on_finished=_finished)
    except RebuildInProgressError:  # pragma: no cover - 已被前置检查覆盖
        thread = None
    if thread is None:
        _tasks.pop(task_id, None)
        raise HTTPException(
            status_code=409,
            detail={"code": "REBUILD_IN_PROGRESS", "message": "已有索引重建任务正在执行。"},
        )

    # 重建在 store 内部后台线程执行，API 立即返回 202。
    return {"task_id": task_id, "status": "RUNNING"}


@router.get("/v1/wiki/reindex/{task_id}")
def reindex_status(task_id: str):
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "TASK_NOT_FOUND", "message": "重建任务不存在。"},
        )
    return task
