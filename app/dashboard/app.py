from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import load_config
from app.events import EventBus, NarrativeDetected, PerformanceUpdated
from app.dashboard.service import DashboardService


DASHBOARD_DIR = Path(__file__).resolve().parent


class DashboardEventState:
    def __init__(self) -> None:
        self.last_event_at: str | None = None

    def handle(self, event: PerformanceUpdated | NarrativeDetected) -> None:
        self.last_event_at = datetime.now(timezone.utc).isoformat()


def create_app(
    database_path: Path | None = None,
    event_bus: EventBus | None = None,
) -> FastAPI:
    path = database_path or load_config().database_path
    service = DashboardService(path)
    event_state = DashboardEventState()
    if event_bus is not None:
        event_bus.subscribe(PerformanceUpdated, event_state.handle)
        event_bus.subscribe(NarrativeDetected, event_state.handle)

    app = FastAPI(
        title="x-narrative-tracker Analytics",
        version="1.0.0",
        description="Read-only analytics dashboard for crypto narrative signals.",
    )
    app.state.dashboard_service = service
    app.state.dashboard_events = event_state
    app.mount(
        "/static",
        StaticFiles(directory=DASHBOARD_DIR / "static"),
        name="static",
    )
    templates = Jinja2Templates(directory=DASHBOARD_DIR / "templates")

    def render(request: Request, template: str, page: str, **context):
        return templates.TemplateResponse(
            request=request,
            name=template,
            context={"page": page, **context},
        )

    @app.get("/", response_class=HTMLResponse)
    def overview_page(request: Request):
        return render(
            request,
            "overview.html",
            "overview",
            data=service.overview(),
        )

    @app.get("/signals", response_class=HTMLResponse)
    def signals_page(request: Request):
        return render(
            request,
            "signals.html",
            "signals",
            signals=service.signals(),
            status=service.status(),
        )

    @app.get("/performance", response_class=HTMLResponse)
    def performance_page(request: Request):
        return render(
            request,
            "performance.html",
            "performance",
            performance=service.performance(),
            status=service.status(),
        )

    @app.get("/narratives", response_class=HTMLResponse)
    def narratives_page(request: Request):
        return render(
            request,
            "rankings.html",
            "narratives",
            title="Narratives",
            endpoint="/api/narratives",
            rankings=service.narratives(),
            status=service.status(),
        )

    @app.get("/tokens", response_class=HTMLResponse)
    def tokens_page(request: Request):
        return render(
            request,
            "rankings.html",
            "tokens",
            title="Tokens",
            endpoint="/api/tokens",
            rankings=service.tokens(),
            status=service.status(),
        )

    @app.get("/api/signals")
    def signals_api(limit: int = Query(default=50, ge=1, le=200)):
        return {"signals": service.signals(limit)}

    @app.get("/api/performance")
    def performance_api():
        return service.performance()

    @app.get("/api/narratives")
    def narratives_api(limit: int = Query(default=25, ge=1, le=100)):
        return {"narratives": service.narratives(limit)}

    @app.get("/api/tokens")
    def tokens_api(limit: int = Query(default=25, ge=1, le=100)):
        return {"tokens": service.tokens(limit)}

    @app.get("/api/status")
    def status_api():
        status = service.status()
        status["last_event_at"] = event_state.last_event_at
        return status

    return app


app = create_app()
