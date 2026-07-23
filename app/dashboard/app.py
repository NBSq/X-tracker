from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
import sqlite3

from app.config import load_config
from app.analytics.historical import HistoricalThresholds
from app.events import EventBus, NarrativeDetected, PerformanceUpdated
from app.dashboard.service import DashboardService
from app.rules import RuleValidationError


DASHBOARD_DIR = Path(__file__).resolve().parent
HISTORY_PERIODS = {"7d", "30d", "90d", "all"}


class RuleCreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    priority: int = 0
    condition: dict
    action: list[str] | str


class RuleUpdatePayload(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool | None = None
    priority: int | None = None
    condition: dict | None = None
    action: list[str] | str | None = None


class DashboardEventState:
    def __init__(self) -> None:
        self.last_event_at: str | None = None

    def handle(self, event: PerformanceUpdated | NarrativeDetected) -> None:
        self.last_event_at = datetime.now(timezone.utc).isoformat()


def create_app(
    database_path: Path | None = None,
    event_bus: EventBus | None = None,
) -> FastAPI:
    config = load_config()
    path = database_path or config.database_path
    service = DashboardService(
        path,
        HistoricalThresholds(
            growth_percent=config.history_growth_threshold,
            minimum_activity=config.history_minimum_activity,
        ),
    )
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

    def valid_period(period: str) -> str:
        if period not in HISTORY_PERIODS:
            raise HTTPException(
                status_code=400,
                detail="period must be one of: 7d, 30d, 90d, all",
            )
        return period

    def rule_error(exc: Exception) -> HTTPException:
        if isinstance(exc, KeyError):
            return HTTPException(status_code=404, detail="Rule not found")
        if isinstance(exc, sqlite3.IntegrityError):
            return HTTPException(status_code=409, detail="Rule name already exists")
        return HTTPException(status_code=422, detail=str(exc))

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

    @app.get("/history", response_class=HTMLResponse)
    def history_page(request: Request, period: str = "30d"):
        selected = valid_period(period)
        return render(
            request,
            "history.html",
            "history",
            history=service.history(selected),
            selected_period=selected,
            status=service.status(),
        )

    @app.get("/history/narratives/{name:path}", response_class=HTMLResponse)
    def narrative_history_page(request: Request, name: str, period: str = "30d"):
        selected = valid_period(period)
        detail = service.history_detail("narrative", name, selected)
        if detail is None:
            raise HTTPException(status_code=404, detail="Narrative not found")
        return render(
            request,
            "history_detail.html",
            "history",
            detail=detail,
            kind="Narrative",
            selected_period=selected,
            status=service.status(),
        )

    @app.get("/history/tokens/{symbol:path}", response_class=HTMLResponse)
    def token_history_page(request: Request, symbol: str, period: str = "30d"):
        selected = valid_period(period)
        detail = service.history_detail("token", symbol, selected)
        if detail is None:
            raise HTTPException(status_code=404, detail="Token not found")
        return render(
            request,
            "history_detail.html",
            "history",
            detail=detail,
            kind="Token",
            selected_period=selected,
            status=service.status(),
        )

    @app.get("/outcomes", response_class=HTMLResponse)
    def outcomes_page(
        request: Request,
        status: str | None = Query(default=None, pattern="^(SUCCESS|NEUTRAL|FAILED)$"),
        window: int | None = Query(default=None, ge=1),
        token: str | None = None,
        narrative: str | None = None,
    ):
        filters = {
            "status": status,
            "window": window,
            "token": token,
            "narrative": narrative,
        }
        return render(
            request,
            "outcomes.html",
            "outcomes",
            outcomes=service.outcomes(
                status=status,
                evaluation_window_hours=window,
                token=token,
                narrative=narrative,
            ),
            summary=service.outcome_summary(),
            filters=filters,
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

    @app.get("/rules", response_class=HTMLResponse)
    def rules_page(request: Request):
        return render(
            request,
            "rules.html",
            "rules",
            rules=service.rules(),
            status=service.status(),
        )

    @app.get("/rules/{rule_id}", response_class=HTMLResponse)
    def rule_detail_page(request: Request, rule_id: int):
        rule = service.rule(rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail="Rule not found")
        return render(
            request,
            "rule_detail.html",
            "rules",
            rule=rule,
            status=service.status(),
        )

    @app.get("/api/signals")
    def signals_api(limit: int = Query(default=50, ge=1, le=200)):
        return {"signals": service.signals(limit)}

    @app.get("/api/performance")
    def performance_api():
        return service.performance()

    @app.get("/api/history/summary")
    def history_summary_api(period: str = "30d"):
        data = service.history(valid_period(period))
        return {
            "period": data["period"],
            "generated_at": data["generated_at"],
            "summary": data["summary"],
        }

    @app.get("/api/history/timeline")
    def history_timeline_api(period: str = "30d"):
        data = service.history(valid_period(period))
        return {"period": data["period"], "timeline": data["timeline"]}

    @app.get("/api/history/narratives")
    def history_narratives_api(period: str = "30d"):
        data = service.history(valid_period(period))
        return {"period": data["period"], "narratives": data["narratives"]}

    @app.get("/api/history/tokens")
    def history_tokens_api(period: str = "30d"):
        data = service.history(valid_period(period))
        return {"period": data["period"], "tokens": data["tokens"]}

    @app.get("/api/history/narratives/{name:path}")
    def history_narrative_detail_api(name: str, period: str = "30d"):
        detail = service.history_detail("narrative", name, valid_period(period))
        if detail is None:
            raise HTTPException(status_code=404, detail="Narrative not found")
        return detail

    @app.get("/api/history/tokens/{symbol:path}")
    def history_token_detail_api(symbol: str, period: str = "30d"):
        detail = service.history_detail("token", symbol, valid_period(period))
        if detail is None:
            raise HTTPException(status_code=404, detail="Token not found")
        return detail

    @app.get("/api/outcomes")
    def outcomes_api(
        limit: int = Query(default=100, ge=1, le=500),
        status: str | None = Query(default=None, pattern="^(SUCCESS|NEUTRAL|FAILED)$"),
        evaluation_window_hours: int | None = Query(default=None, ge=1),
        token: str | None = None,
        narrative: str | None = None,
        period_hours: int | None = Query(default=None, ge=1),
    ):
        return {"outcomes": service.outcomes(
            limit=limit,
            status=status,
            evaluation_window_hours=evaluation_window_hours,
            token=token,
            narrative=narrative,
            period_hours=period_hours,
        )}

    @app.get("/api/outcomes/summary")
    def outcomes_summary_api(
        period_hours: int | None = Query(default=None, ge=1),
    ):
        return service.outcome_summary(period_hours)

    @app.get("/api/outcomes/{signal_id}")
    def signal_outcomes_api(signal_id: int):
        return {"signal_id": signal_id, "outcomes": service.outcomes(signal_id=signal_id)}

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

    @app.get("/api/rules")
    def rules_api(enabled: bool | None = None):
        return {"rules": service.rules(enabled)}

    @app.post("/api/rules", status_code=status.HTTP_201_CREATED)
    def create_rule_api(payload: RuleCreatePayload):
        try:
            return service.create_rule(
                payload.name,
                payload.condition,
                payload.action,
                payload.enabled,
                payload.priority,
            )
        except (RuleValidationError, sqlite3.IntegrityError) as exc:
            raise rule_error(exc) from exc

    @app.put("/api/rules/{rule_id}")
    def update_rule_api(rule_id: int, payload: RuleUpdatePayload):
        changes = {
            key: value
            for key, value in payload.model_dump().items()
            if value is not None
        }
        try:
            return service.update_rule(rule_id, **changes)
        except (KeyError, RuleValidationError, sqlite3.IntegrityError) as exc:
            raise rule_error(exc) from exc

    @app.delete("/api/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_rule_api(rule_id: int):
        if not service.delete_rule(rule_id):
            raise HTTPException(status_code=404, detail="Rule not found")

    return app


app = create_app()
