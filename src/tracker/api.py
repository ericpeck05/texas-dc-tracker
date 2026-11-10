"""JSON API for the web app, and the built frontend (web/dist) when it exists.

Run with `tracker serve`. Everything is read-only except POST /api/review/{link_id}, the
review queue's confirm/reject, which writes a decision to the database and to
seeds/review_decisions.csv. Keep the server on 127.0.0.1.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.engine import Engine

from tracker.config import get_settings
from tracker.db.session import init_db, make_engine, session_scope
from tracker.review import DECISIONS_PATH, KINDS, ReviewError, review_and_save, review_queue
from tracker.views.feed import feed
from tracker.views.sites import site_list, site_view
from tracker.views.ticker import ticker_index, ticker_view

DISCLAIMER = (
    "Built from public filings. Links between sites and companies carry confidence levels "
    "and can be wrong. Not investment advice."
)
WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


class Decision(BaseModel):
    decision: str  # confirm | reject
    note: str | None = None


def create_app(
    engine: Engine | None = None,
    web_dist: Path | None = WEB_DIST,
    decisions_path: Path = DECISIONS_PATH,
) -> FastAPI:
    engine = engine or make_engine(get_settings().db_url)
    init_db(engine)  # adds tables a newer version needs (review_decision); never alters data
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

    @app.get("/api/feed")
    def filings(days: int = 30, type: str | None = None) -> dict:
        with session_scope(engine) as session:
            return {"disclaimer": DISCLAIMER, **feed(session, days=days or None, signal_type=type)}

    @app.get("/api/review")
    def review(kind: str | None = None, limit: int = 200) -> dict:
        if kind is not None and kind not in KINDS:
            raise HTTPException(400, f"kind must be one of {', '.join(KINDS)}")
        with session_scope(engine) as session:
            return review_queue(session, kind, limit)

    @app.post("/api/review/{link_id}")
    def decide(link_id: int, body: Decision) -> dict:
        try:
            with session_scope(engine) as session:
                return review_and_save(session, link_id, body.decision, body.note, decisions_path)
        except ReviewError as e:
            raise HTTPException(409, str(e)) from e

    if web_dist is not None and (web_dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=web_dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            return FileResponse(web_dist / "index.html")

    return app
