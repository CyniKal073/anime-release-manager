"""V1：本地 Web UI。

启动::

    python -m src.api.app            # 默认 http://127.0.0.1:8765
    python -m uvicorn src.api.app:app --port 8765
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config import ConfigError, Settings, load_dotenv_best_effort
from ..nekobt.client import NekoBTError
from ..qbittorrent.client import QBittorrentError
from ..release.matcher import ReleasePreferences, default_preferences
from ..service import (
    ServiceContext,
    build_context,
    confirm_mapping,
    health,
    list_releases,
    load_preferences,
    preferences_from_dict,
    preferences_to_dict,
    resolve_work,
    save_preferences,
    submit_download,
)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
DEFAULT_PORT = 8765

_CONTEXT: Optional[ServiceContext] = None


def get_context() -> ServiceContext:
    """依赖注入点：测试里可以用 app.dependency_overrides 替换。"""
    global _CONTEXT
    if _CONTEXT is None:
        _CONTEXT = build_context()
    return _CONTEXT


class DownloadRequest(BaseModel):
    torrent_id: str
    infohash: Optional[str] = None
    title: Optional[str] = None
    paused: bool = False


class PreferencesRequest(BaseModel):
    resolutions: List[str] = []
    sources: List[str] = []
    video_codecs: List[str] = []
    sub_langs: List[str] = []
    require_batch: Optional[bool] = None
    min_seeders: Optional[int] = None


class MappingRequest(BaseModel):
    input_title: str
    media_id: str
    anilist_id: Optional[int] = None
    bangumi_id: Optional[int] = None
    confidence: float = 1.0


def create_app() -> FastAPI:
    app = FastAPI(title="Anime Release Manager", version="0.2.0")

    @app.get("/api/health")
    def api_health(ctx: ServiceContext = Depends(get_context)) -> Dict[str, Any]:
        return health(ctx)

    @app.get("/api/search")
    def api_search(
        title: str,
        top: int = 5,
        ctx: ServiceContext = Depends(get_context),
    ) -> Dict[str, Any]:
        if not title.strip():
            raise HTTPException(status_code=400, detail="title 不能为空")
        try:
            return resolve_work(ctx, title, top=max(1, min(top, 20)))
        except NekoBTError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/mapping")
    def api_confirm_mapping(
        req: MappingRequest,
        ctx: ServiceContext = Depends(get_context),
    ) -> Dict[str, Any]:
        """用户确认候选后落库，下次同一输入直接命中缓存。"""
        return confirm_mapping(
            ctx,
            input_title=req.input_title,
            media_id=req.media_id,
            anilist_id=req.anilist_id,
            bangumi_id=req.bangumi_id,
            confidence=req.confidence,
        )

    @app.get("/api/media/{media_id}/releases")
    def api_releases(
        media_id: str,
        resolution: List[str] = Query(default=[]),
        source: List[str] = Query(default=[]),
        codec: List[str] = Query(default=[]),
        sub_lang: List[str] = Query(default=[]),
        batch: Optional[bool] = None,
        min_seeders: Optional[int] = None,
        limit: int = 100,
        sort: str = "preference",
        include_unknown: bool = True,
        ctx: ServiceContext = Depends(get_context),
    ) -> Dict[str, Any]:
        provided = any(
            [resolution, source, codec, sub_lang, batch is not None, min_seeders is not None]
        )
        if provided:
            prefs = ReleasePreferences(
                resolutions=list(resolution),
                sources=list(source),
                video_codecs=list(codec),
                sub_langs=list(sub_lang),
                require_batch=batch,
                min_seeders=min_seeders,
            )
        else:
            prefs = load_preferences(ctx.store)
        try:
            return list_releases(
                ctx,
                media_id,
                prefs=prefs,
                limit=max(1, min(limit, 300)),
                sort=sort,
                include_unknown=include_unknown,
            )
        except NekoBTError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/download")
    def api_download(
        req: DownloadRequest,
        ctx: ServiceContext = Depends(get_context),
    ) -> Dict[str, Any]:
        try:
            return submit_download(
                ctx,
                req.torrent_id,
                paused=req.paused,
                infohash=req.infohash,
                title=req.title,
            )
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (NekoBTError, QBittorrentError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/downloads")
    def api_downloads(
        limit: int = 20,
        ctx: ServiceContext = Depends(get_context),
    ) -> Dict[str, Any]:
        rows = ctx.store.list_downloads(max(1, min(limit, 200)))
        return {"items": [dict(row) for row in rows]}

    @app.get("/api/preferences")
    def api_get_preferences(ctx: ServiceContext = Depends(get_context)) -> Dict[str, Any]:
        return {
            "preferences": preferences_to_dict(load_preferences(ctx.store)),
            "defaults": preferences_to_dict(default_preferences()),
        }

    @app.post("/api/preferences")
    def api_set_preferences(
        req: PreferencesRequest,
        ctx: ServiceContext = Depends(get_context),
    ) -> Dict[str, Any]:
        payload = req.model_dump() if hasattr(req, "model_dump") else req.dict()
        prefs = preferences_from_dict(payload)
        save_preferences(ctx.store, prefs)
        return {"ok": True, "preferences": preferences_to_dict(prefs)}

    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/")
    def index() -> FileResponse:
        index_file = WEB_DIR / "index.html"
        if not index_file.exists():
            raise HTTPException(status_code=500, detail=f"缺少 {index_file}")
        return FileResponse(index_file)

    return app


app = create_app()


def main() -> int:
    import uvicorn

    parser = argparse.ArgumentParser(prog="anime-release-manager-web")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--no-access-log", action="store_true", help="关闭每条请求的访问日志")
    args = parser.parse_args()

    load_dotenv_best_effort()

    # 日志被重定向到文件时，中文和 ANSI 颜色码都会变成乱码。
    # 这里固定 UTF-8，并在下面关掉颜色输出。
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    print(f"Web UI: http://{args.host}:{args.port}")
    uvicorn.run(
        "src.api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
        use_colors=False,
        access_log=not args.no_access_log,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
