"""JSON API for the web app, and the built frontend (web/dist) when it exists.

Run with `tracker serve`. Read-only: nothing here writes to the database.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.engine import Engine

from tracker.config import get_settings
from tracker.db.session import make_engine, session_scope
from tracker.views.sites import site_list, site_view
from tracker.views.ticker import ticker_index, ticker_view

DISCLAIMER = (
    "Built from public filings. Links between sites and companies carry confidence levels "
    "and can be wrong. Not investment advice."
)
WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


def create_app(engine: Engine | None = None, web_dist: Path | None = WEB_DIST) -> FastAPI:
    engine = engine or make_engine(get_settings().db_url)
    app = FastAPI(title="Texas Data Center Tracker", docs_url="/api/docs")

    @app.get("/api/tickers")
    def tickers() -> dict:
        with session_scope(engine) as session:
            return {"disclaimer": DISCLAIMER, "tickers": ticker_index(session)}

    @app.get("/api/tickers/{symbol}")
    def ticker(symbol: str) -> dict:
        with session_scope(engine) as session:
            view = ticker_view(session, symbol)
        if view is None:
            raise HTTPException(404, f"No company or supplier with ticker {symbol.upper()}")
        return {"disclaimer": DISCLAIMER, **view}

    @app.get("/api/sites")
    def sites() -> dict:
        with session_scope(engine) as session:
            return {"disclaimer": DISCLAIMER, "sites": site_list(session)}

    @app.get("/api/sites/{site_id}")
    def site(site_id: int) -> dict:
        with session_scope(engine) as session:
            view = site_view(session, site_id)
        if view is None:
            raise HTTPException(404, f"No site {site_id}")
        return {"disclaimer": DISCLAIMER, **view}

    if web_dist is not None and (web_dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=web_dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            return FileResponse(web_dist / "index.html")

    return app
