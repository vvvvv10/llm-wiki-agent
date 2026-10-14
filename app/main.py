"""FastAPI 应用入口。

启动时同步构建索引；构建失败不阻断启动，通过 ``/health`` 暴露 ``index_error``。
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from app.api import chat, wiki
from app.config import Settings, get_settings
from app.generation.gateway import ModelGateway
from app.ingestion.chunker import MarkdownChunker
from app.retrieval.index_store import IndexStore
from app.service.answer_service import AnswerService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent / "web"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        gateway = getattr(app.state, "gateway", None)
        if gateway is not None:
            gateway.close()

    app = FastAPI(title="LLM Wiki Agent", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.index_store = None
    app.state.answer_service = None
    app.state.gateway = None
    app.state.index_error = None

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "INTERNAL_ERROR", "message": "服务内部错误。"}},
        )

    app.include_router(chat.router)
    app.include_router(wiki.router)

    @app.get("/health")
    def health():
        store: IndexStore | None = app.state.index_store
        snapshot = store.snapshot if store else None
        return {
            "status": "ok",
            "index_ready": snapshot is not None,
            "documents": len(snapshot.documents) if snapshot else 0,
            "chunks": len(snapshot.chunks) if snapshot else 0,
            "index_version": snapshot.version if snapshot else None,
            "model_configured": settings.model_configured,
            "index_error": app.state.index_error,
        }

    @app.get("/", include_in_schema=False)
    def index_page():
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/admin", include_in_schema=False)
    def admin_page():
        return FileResponse(WEB_DIR / "admin.html")

    _build_index_and_service(app, settings)
    return app


def _build_index_and_service(app: FastAPI, settings: Settings) -> None:
    store = IndexStore(
        settings.wiki_root,
        chunker=MarkdownChunker(
            max_chars=settings.chunk_max_chars,
            overlap_chars=settings.chunk_overlap_chars,
        ),
    )
    app.state.index_store = store
    try:
        store.rebuild()
    except Exception:
        app.state.index_error = "INDEX_BUILD_FAILED"
        logger.exception("initial index build failed; service will still start")

    if not settings.model_configured:
        logger.warning("model is not fully configured; chat requests will fail until .env is set")

    gateway = ModelGateway(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
        enable_thinking=settings.llm_enable_thinking,
        max_tokens=settings.llm_max_tokens,
    )
    app.state.gateway = gateway

    app.state.answer_service = AnswerService(
        index_store=store,
        gateway=gateway,
        top_k=settings.retrieval_top_k,
        min_score=settings.retrieval_min_score,
        max_context_chars=settings.max_context_chars,
        history_max_messages=settings.history_max_messages,
    )


app = create_app()
