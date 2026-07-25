from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
import sqlite3

from app.ai.base import SignalAnalysisUnavailable
from app.config import Config, load_config
from app.analytics.historical import HistoricalThresholds
from app.events import EventBus, NarrativeDetected, PerformanceUpdated, WatchlistMatched
from app.dashboard.service import DashboardService
from app.rules import RuleValidationError
from app.watchlists import WatchlistValidationError
from app.ingestion.service import MultiSourceIngestionService


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


class WatchlistCreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    enabled: bool = True
    priority: int = Field(default=0, ge=0, le=100)
    minimum_hype_score: float = Field(default=0, ge=0, le=100)
    minimum_momentum_score: float = Field(default=0, ge=0, le=100)
    minimum_confidence: int = Field(default=0, ge=0, le=10)
    telegram_enabled: bool = True
    include_in_digest: bool = False
    dashboard_highlight: bool = True
    case_insensitive: bool = True


class WatchlistUpdatePayload(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=100)
    minimum_hype_score: float | None = Field(default=None, ge=0, le=100)
    minimum_momentum_score: float | None = Field(default=None, ge=0, le=100)
    minimum_confidence: int | None = Field(default=None, ge=0, le=10)
    telegram_enabled: bool | None = None
    include_in_digest: bool | None = None
    dashboard_highlight: bool | None = None
    case_insensitive: bool | None = None


class WatchlistItemPayload(BaseModel):
    item_type: str = Field(pattern="^(token|narrative)$")
    item_value: str = Field(min_length=1, max_length=120)


class SourceCreatePayload(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    type: str = Field(pattern="^(rss|atom|feed|local_json)$")
    url: str = Field(min_length=1, max_length=2000)
    enabled: bool = True
    priority: int = Field(default=5, ge=0, le=10)
    categories: list[str] = Field(default_factory=list)
    fetch_interval_seconds: int = Field(default=300, ge=1)


class SourceUpdatePayload(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    source_type: str | None = Field(default=None, pattern="^(rss|atom|feed|local_json)$")
    url: str | None = Field(default=None, min_length=1, max_length=2000)
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=10)
    fetch_interval_seconds: int | None = Field(default=None, ge=1)


class DashboardEventState:
    def __init__(self) -> None:
        self.last_event_at: str | None = None

    def handle(
        self,
        event: PerformanceUpdated | NarrativeDetected | WatchlistMatched,
    ) -> None:
        self.last_event_at = datetime.now(timezone.utc).isoformat()


def create_app(
    database_path: Path | None = None,
    event_bus: EventBus | None = None,
    config: Config | None = None,
) -> FastAPI:
    app_config = config or load_config()
    path = database_path or app_config.database_path
    service = DashboardService(
        path,
        HistoricalThresholds(
            growth_percent=app_config.history_growth_threshold,
            minimum_activity=app_config.history_minimum_activity,
        ),
        config=app_config,
    )
    event_state = DashboardEventState()
    if event_bus is not None:
        event_bus.subscribe(PerformanceUpdated, event_state.handle)
        event_bus.subscribe(NarrativeDetected, event_state.handle)
        event_bus.subscribe(WatchlistMatched, event_state.handle)

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

    def watchlist_error(exc: Exception) -> HTTPException:
        if isinstance(exc, KeyError):
            return HTTPException(status_code=404, detail="Watchlist not found")
        if isinstance(exc, sqlite3.IntegrityError):
            return HTTPException(
                status_code=409,
                detail="Watchlist name or item already exists",
            )
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
    def signals_page(request: Request, watchlist_id: int | None = None):
        return render(
            request,
            "signals.html",
            "signals",
            signals=service.signals(watchlist_id=watchlist_id),
            watchlists=service.watchlists(),
            selected_watchlist_id=watchlist_id,
            status=service.status(),
        )

    @app.get("/signals/{signal_id}", response_class=HTMLResponse)
    def signal_detail_page(request: Request, signal_id: int):
        signal = service.signal_detail(signal_id)
        if signal is None:
            raise HTTPException(status_code=404, detail="Signal not found")
        return render(
            request,
            "signal_detail.html",
            "signals",
            signal=signal,
            status=service.status(),
        )

    @app.get("/ai", response_class=HTMLResponse)
    def ai_page(request: Request):
        return render(
            request,
            "ai.html",
            "ai",
            ai_status=service.ai_status(),
            analyses=service.ai_analyses(50),
            usage=service.ai_usage(50),
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
    def history_page(
        request: Request,
        period: str = "30d",
        watchlist_id: int | None = None,
    ):
        selected = valid_period(period)
        return render(
            request,
            "history.html",
            "history",
            history=service.history(selected, watchlist_id),
            selected_period=selected,
            watchlists=service.watchlists(),
            selected_watchlist_id=watchlist_id,
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
        watchlist_id: int | None = None,
    ):
        filters = {
            "status": status,
            "window": window,
            "token": token,
            "narrative": narrative,
            "watchlist_id": watchlist_id,
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
                watchlist_id=watchlist_id,
            ),
            summary=service.outcome_summary(watchlist_id=watchlist_id),
            filters=filters,
            watchlists=service.watchlists(),
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

    @app.get("/watchlists", response_class=HTMLResponse)
    def watchlists_page(request: Request):
        return render(
            request,
            "watchlists.html",
            "watchlists",
            watchlists=service.watchlists(),
            status=service.status(),
        )

    @app.get("/watchlists/new", response_class=HTMLResponse)
    def watchlist_create_page(request: Request):
        return render(
            request,
            "watchlist_form.html",
            "watchlists",
            watchlist=None,
            status=service.status(),
        )

    @app.get("/watchlists/{watchlist_id}", response_class=HTMLResponse)
    def watchlist_detail_page(request: Request, watchlist_id: int):
        watchlist = service.watchlist(watchlist_id)
        if watchlist is None:
            raise HTTPException(status_code=404, detail="Watchlist not found")
        return render(
            request,
            "watchlist_detail.html",
            "watchlists",
            report=watchlist,
            status=service.status(),
        )

    @app.get("/watchlists/{watchlist_id}/edit", response_class=HTMLResponse)
    def watchlist_edit_page(request: Request, watchlist_id: int):
        watchlist = service.watchlist(watchlist_id)
        if watchlist is None:
            raise HTTPException(status_code=404, detail="Watchlist not found")
        return render(
            request,
            "watchlist_form.html",
            "watchlists",
            watchlist=watchlist["watchlist"],
            status=service.status(),
        )

    @app.get("/sources", response_class=HTMLResponse)
    def sources_page(request: Request):
        return render(
            request, "sources.html", "sources", sources=service.sources(),
            status=service.status(),
        )

    @app.get("/sources/{source_id}", response_class=HTMLResponse)
    def source_detail_page(request: Request, source_id: int):
        detail = service.source_detail(source_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Source not found")
        return render(
            request, "source_detail.html", "sources", detail=detail,
            status=service.status(),
        )

    @app.get("/unified-events", response_class=HTMLResponse)
    def unified_events_page(
        request: Request,
        source_id: int | None = None,
        token: str | None = None,
        narrative: str | None = None,
        status_filter: str | None = Query(default=None, alias="status"),
        from_date: str | None = None,
        to_date: str | None = None,
    ):
        filters = {
            "source_id": source_id, "token": token, "narrative": narrative,
            "status": status_filter, "from_date": from_date, "to_date": to_date,
        }
        return render(
            request, "unified_events.html", "unified_events",
            events=service.unified_events(
                source_id=source_id, token=token, narrative=narrative,
                status=status_filter, from_date=from_date, to_date=to_date,
            ),
            sources=service.sources(), filters=filters, status=service.status(),
        )

    @app.get("/unified-events/{event_id}", response_class=HTMLResponse)
    def unified_event_detail_page(request: Request, event_id: int):
        detail = service.unified_event_detail(event_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Unified event not found")
        return render(
            request, "unified_event_detail.html", "unified_events", detail=detail,
            status=service.status(),
        )

    @app.get("/deduplication", response_class=HTMLResponse)
    def deduplication_page(request: Request):
        return render(
            request, "deduplication.html", "deduplication",
            deduplication=service.deduplication(), status=service.status(),
        )

    @app.get("/api/signals")
    def signals_api(
        limit: int = Query(default=50, ge=1, le=200),
        watchlist_id: int | None = None,
    ):
        return {"signals": service.signals(limit, watchlist_id)}

    @app.get("/api/ai/status")
    def ai_status_api():
        return service.ai_status()

    @app.get("/api/ai/usage")
    def ai_usage_api(limit: int = Query(default=100, ge=1, le=500)):
        return {"usage": service.ai_usage(limit)}

    @app.get("/api/ai/analyses")
    def ai_analyses_api(
        limit: int = Query(default=100, ge=1, le=500),
        provider: str | None = Query(default=None, pattern="^(mock|openai)$"),
    ):
        return {"analyses": service.ai_analyses(limit, provider)}

    @app.get("/api/signals/{signal_id}/analysis")
    def signal_analysis_api(signal_id: int):
        if service.signal_detail(signal_id) is None:
            raise HTTPException(status_code=404, detail="Signal not found")
        return {"signal_id": signal_id, "analysis": service.signal_analysis(signal_id)}

    @app.post("/api/signals/{signal_id}/analysis")
    def analyze_signal_api(signal_id: int):
        if service.signal_detail(signal_id) is None:
            raise HTTPException(status_code=404, detail="Signal not found")
        try:
            return {"signal_id": signal_id, "analysis": service.analyze_signal(signal_id)}
        except SignalAnalysisUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/performance")
    def performance_api():
        return service.performance()

    @app.get("/api/history/summary")
    def history_summary_api(period: str = "30d", watchlist_id: int | None = None):
        data = service.history(valid_period(period), watchlist_id)
        return {
            "period": data["period"],
            "generated_at": data["generated_at"],
            "summary": data["summary"],
        }

    @app.get("/api/history/timeline")
    def history_timeline_api(period: str = "30d", watchlist_id: int | None = None):
        data = service.history(valid_period(period), watchlist_id)
        return {"period": data["period"], "timeline": data["timeline"]}

    @app.get("/api/history/narratives")
    def history_narratives_api(period: str = "30d", watchlist_id: int | None = None):
        data = service.history(valid_period(period), watchlist_id)
        return {"period": data["period"], "narratives": data["narratives"]}

    @app.get("/api/history/tokens")
    def history_tokens_api(period: str = "30d", watchlist_id: int | None = None):
        data = service.history(valid_period(period), watchlist_id)
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
        watchlist_id: int | None = None,
    ):
        return {"outcomes": service.outcomes(
            limit=limit,
            status=status,
            evaluation_window_hours=evaluation_window_hours,
            token=token,
            narrative=narrative,
            period_hours=period_hours,
            watchlist_id=watchlist_id,
        )}

    @app.get("/api/outcomes/summary")
    def outcomes_summary_api(
        period_hours: int | None = Query(default=None, ge=1),
        watchlist_id: int | None = None,
    ):
        return service.outcome_summary(period_hours, watchlist_id)

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

    @app.get("/api/watchlists")
    def watchlists_api(enabled: bool | None = None):
        return {"watchlists": service.watchlists(enabled)}

    @app.post("/api/watchlists", status_code=status.HTTP_201_CREATED)
    def create_watchlist_api(payload: WatchlistCreatePayload):
        try:
            return service.create_watchlist(**payload.model_dump())
        except (WatchlistValidationError, sqlite3.IntegrityError) as exc:
            raise watchlist_error(exc) from exc

    @app.get("/api/watchlists/{watchlist_id}")
    def watchlist_api(watchlist_id: int):
        result = service.watchlist(watchlist_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Watchlist not found")
        return result

    @app.put("/api/watchlists/{watchlist_id}")
    def update_watchlist_api(watchlist_id: int, payload: WatchlistUpdatePayload):
        changes = {
            key: value
            for key, value in payload.model_dump().items()
            if value is not None
        }
        try:
            return service.update_watchlist(watchlist_id, **changes)
        except (KeyError, WatchlistValidationError, sqlite3.IntegrityError) as exc:
            raise watchlist_error(exc) from exc

    @app.delete(
        "/api/watchlists/{watchlist_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def delete_watchlist_api(watchlist_id: int):
        if not service.delete_watchlist(watchlist_id):
            raise HTTPException(status_code=404, detail="Watchlist not found")

    @app.post(
        "/api/watchlists/{watchlist_id}/items",
        status_code=status.HTTP_201_CREATED,
    )
    def add_watchlist_item_api(watchlist_id: int, payload: WatchlistItemPayload):
        try:
            return service.add_watchlist_item(
                watchlist_id,
                payload.item_type,
                payload.item_value,
            )
        except (KeyError, WatchlistValidationError, sqlite3.IntegrityError) as exc:
            raise watchlist_error(exc) from exc

    @app.delete(
        "/api/watchlists/{watchlist_id}/items/{item_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def remove_watchlist_item_api(watchlist_id: int, item_id: int):
        try:
            removed = service.remove_watchlist_item(watchlist_id, item_id)
        except KeyError as exc:
            raise watchlist_error(exc) from exc
        if not removed:
            raise HTTPException(status_code=404, detail="Watchlist item not found")

    @app.get("/api/watchlists/{watchlist_id}/signals")
    def watchlist_signals_api(
        watchlist_id: int,
        limit: int = Query(default=50, ge=1, le=200),
    ):
        if service.watchlist(watchlist_id) is None:
            raise HTTPException(status_code=404, detail="Watchlist not found")
        return {"signals": service.signals(limit, watchlist_id)}

    @app.get("/api/watchlists/{watchlist_id}/performance")
    def watchlist_performance_api(watchlist_id: int):
        result = service.watchlist(watchlist_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Watchlist not found")
        return result

    @app.get("/api/watchlists/{watchlist_id}/history")
    def watchlist_history_api(watchlist_id: int, period: str = "30d"):
        if service.watchlist(watchlist_id) is None:
            raise HTTPException(status_code=404, detail="Watchlist not found")
        return service.history(valid_period(period), watchlist_id)

    @app.get("/api/sources")
    def sources_api():
        return {"sources": service.sources()}

    @app.post("/api/sources", status_code=status.HTTP_201_CREATED)
    def create_source_api(payload: SourceCreatePayload):
        try:
            return service.create_source(payload.model_dump())
        except (ValueError, sqlite3.IntegrityError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/sources/{source_id}")
    def source_api(source_id: int):
        detail = service.source_detail(source_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Source not found")
        return detail

    @app.put("/api/sources/{source_id}")
    def update_source_api(source_id: int, payload: SourceUpdatePayload):
        try:
            return service.update_source(
                source_id, payload.model_dump(exclude_none=True)
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Source not found") from exc

    @app.delete("/api/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_source_api(source_id: int):
        try:
            service.delete_source(source_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=409,
                detail="Source not found or has retained content items",
            ) from exc

    @app.post("/api/sources/{source_id}/fetch")
    def fetch_source_api(source_id: int):
        try:
            return service.fetch_source(source_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Source not found") from exc

    @app.post("/api/sources/{source_id}/enable")
    def enable_source_api(source_id: int):
        try:
            return service.update_source(source_id, {"enabled": True})
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Source not found") from exc

    @app.post("/api/sources/{source_id}/disable")
    def disable_source_api(source_id: int):
        try:
            return service.update_source(source_id, {"enabled": False})
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Source not found") from exc

    @app.get("/api/unified-events")
    def unified_events_api(
        source_id: int | None = None,
        token: str | None = None,
        narrative: str | None = None,
        status_filter: str | None = Query(default=None, alias="status"),
        from_date: str | None = None,
        to_date: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
    ):
        return {
            "events": service.unified_events(
                source_id=source_id, token=token, narrative=narrative,
                status=status_filter, from_date=from_date, to_date=to_date,
                limit=limit,
            )
        }

    @app.get("/api/unified-events/{event_id}")
    def unified_event_api(event_id: int):
        detail = service.unified_event_detail(event_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Unified event not found")
        return detail

    @app.get("/api/unified-events/{event_id}/items")
    def unified_event_items_api(event_id: int):
        detail = service.unified_event_detail(event_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Unified event not found")
        return {"items": detail["items"]}

    @app.get("/api/unified-events/{event_id}/history")
    def unified_event_history_api(event_id: int):
        detail = service.unified_event_detail(event_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Unified event not found")
        return {"history": detail["history"]}

    @app.get("/api/deduplication/stats")
    def deduplication_stats_api(days: int = Query(default=30, ge=1, le=3650)):
        return service.deduplication(days)

    @app.post("/api/deduplication/rebuild")
    def deduplication_rebuild_api():
        db = service._database()
        try:
            processed, created = MultiSourceIngestionService(
                db, service.config
            ).events.rebuild()
            return {"processed": processed, "created": created}
        finally:
            db.close()

    return app


app = create_app()
