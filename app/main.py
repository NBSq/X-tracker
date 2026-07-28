from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from app.ai.analyzer import AnalysisResult, LocalAnalyzer, OpenAIAnalyzer, SpikeInsight
from app.ai.factory import create_signal_reasoning_service
from app.analytics.historical import (
    HistoricalAnalyticsService,
    HistoricalThresholds,
    format_historical_report,
)
from app.alerts.telegram import (
    AlertPost,
    DailyDigest,
    HypeAlert,
    MomentumHistoryItem,
    MomentumHistoryReport,
    OpportunityReport,
    OutcomeNarrative,
    PerformanceNarrative,
    SignalOutcomeReport,
    SignalPerformanceReport,
    NarrativeSummary,
    NarrativeGrowth,
    NarrativeTrend,
    SummaryItem,
    TelegramAlerter,
    TrendReport,
    format_hype_alert,
    format_history_report,
    format_opportunity_report,
    format_outcome_report,
    format_performance_report,
    format_daily_digest,
    format_summary,
    format_trend_report,
)
from app.config import Config, load_config
from app.db.database import Database
from app.events import (
    AIAnalysisCompleted,
    EventBus,
    NarrativeDetected,
    RSSFetched,
    SignalCreated,
)
from app.events.subscribers import AIRuleEvaluationSubscriber, register_default_subscribers
from app.export.csv_exporter import CSVExportResult, CSVExportService
from app.scoring.hype_score import (
    HypeCandidate,
    build_hype_signal,
    candidate_overlap,
    normalize_hype_score,
    should_merge_candidates,
)
from app.scoring.momentum_score import NarrativeMomentum, calculate_momentum_score
from app.scoring.opportunity_score import build_opportunity
from app.scoring.signal_outcomes import OutcomeEvaluator, OutcomeThresholds
from app.rules import RuleService, RuleValidationError, SignalFacts
from app.watchlists import (
    WatchlistService,
    WatchlistValidationError,
    format_watchlist_report,
)
from app.sources.local_client import load_sample_posts
from app.sources.rss_client import RSSClient, load_rss_feeds
from app.sources.x_client import XClient, XPost


logger = logging.getLogger("x_narrative_tracker")


@dataclass(frozen=True)
class EnrichedCandidate:
    candidate: HypeCandidate
    rows: list


class Analyzer(Protocol):
    def analyze_post(self, text: str) -> AnalysisResult: ...
    def explain_spike(
        self,
        kind: str,
        name: str,
        hype_score: float,
        top_posts: list[str],
        related_tokens: list[str],
        related_narratives: list[str],
    ) -> SpikeInsight: ...


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD format") from exc


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Track crypto narratives from X posts")
    parser.add_argument(
        "--mode",
        choices=("live", "local", "rss"),
        default="live",
        help="Use X, RSS, or run the offline sample-post MVP",
    )
    parser.add_argument(
        "--no-telegram",
        action="store_true",
        help="Disable Telegram sending and keep console alerts only",
    )
    parser.add_argument(
        "--reset-db",
        action="store_true",
        help="Delete previous analyses and alerts before running",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print a narrative summary after processing and optionally send it to Telegram",
    )
    parser.add_argument(
        "--mock-ai",
        action="store_true",
        help="Use deterministic keyword analysis instead of the OpenAI API",
    )
    parser.add_argument(
        "--trend-report",
        action="store_true",
        help="Print narrative trends from stored history and optionally send to Telegram",
    )
    parser.add_argument(
        "--daily-digest",
        action="store_true",
        help="Print a 24-hour digest and optionally send it to Telegram",
    )
    parser.add_argument(
        "--history-report",
        action="store_true",
        help="Print historical signal analytics",
    )
    parser.add_argument(
        "--period",
        choices=("7d", "30d", "90d", "all"),
        default="30d",
        help="Historical analytics period (default: 30d)",
    )
    parser.add_argument(
        "--top-opportunities",
        action="store_true",
        help="Rank narrative opportunities from stored momentum history",
    )
    parser.add_argument(
        "--performance-report",
        action="store_true",
        help="Print saved signal performance metrics and optionally send to Telegram",
    )
    parser.add_argument(
        "--outcome-report",
        action="store_true",
        help="Evaluate mature signals and print outcome statistics",
    )
    parser.add_argument(
        "--evaluate-signals",
        action="store_true",
        help="Evaluate every saved signal whose configured outcome window is due",
    )
    parser.add_argument(
        "--outcome-period-hours",
        type=positive_int,
        help="Limit the outcome report to evaluations from the last N hours",
    )
    parser.add_argument(
        "--export-signals-csv",
        action="store_true",
        help="Export saved signals to CSV",
    )
    parser.add_argument(
        "--export-outcomes-csv",
        action="store_true",
        help="Export evaluated signal outcomes to CSV",
    )
    parser.add_argument(
        "--export-performance-csv",
        action="store_true",
        help="Export overall and narrative performance CSV files",
    )
    parser.add_argument(
        "--export-history-csv",
        action="store_true",
        help="Export historical summary, timeline, narrative, and token CSV files",
    )
    parser.add_argument(
        "--export-watchlists-csv",
        action="store_true",
        help="Export watchlists and watchlist items to CSV",
    )
    parser.add_argument(
        "--export-watchlist-signals-csv",
        metavar="NAME",
        help="Export signals associated with one watchlist",
    )
    parser.add_argument(
        "--export-csv",
        choices=("signals", "outcomes", "performance", "all"),
        help="Export one CSV dataset or all available datasets",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("exports"),
        help="Directory for generated CSV files (default: exports)",
    )
    parser.add_argument(
        "--from-date",
        type=iso_date,
        help="Include records on or after this date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--to-date",
        type=iso_date,
        help="Include records on or before this date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Keep RSS mode running and poll continuously",
    )
    parser.add_argument("--list-sources", action="store_true", help="List content sources")
    parser.add_argument("--source-status", action="store_true", help="Show source health")
    parser.add_argument("--fetch-source", metavar="ID", help="Fetch one configured source")
    parser.add_argument("--enable-source", metavar="ID", help="Enable a content source")
    parser.add_argument("--disable-source", metavar="ID", help="Disable a content source")
    parser.add_argument(
        "--list-unified-events", action="store_true", help="List recent unified events"
    )
    parser.add_argument(
        "--show-unified-event", type=positive_int, metavar="ID",
        help="Show a unified event with source items and history",
    )
    parser.add_argument(
        "--deduplication-report", action="store_true",
        help="Print multi-source deduplication statistics",
    )
    parser.add_argument(
        "--rebuild-unified-events", action="store_true",
        help="Idempotently associate historical content with unified events",
    )
    parser.add_argument(
        "--export-sources-csv", action="store_true", help="Export content sources"
    )
    parser.add_argument(
        "--export-content-items-csv", action="store_true", help="Export content items"
    )
    parser.add_argument(
        "--export-unified-events-csv", action="store_true", help="Export unified events"
    )
    parser.add_argument(
        "--export-deduplication-csv", "--export-deduplication-report-csv",
        dest="export_deduplication_csv", action="store_true",
        help="Export deduplication statistics",
    )
    parser.add_argument("--graph-summary", action="store_true", help="Print graph summary")
    parser.add_argument(
        "--graph-node", nargs=2, metavar=("NODE_TYPE", "ENTITY_ID"),
        help="Show one graph node and its relationships",
    )
    parser.add_argument("--graph-top-narratives", action="store_true")
    parser.add_argument("--graph-top-tokens", action="store_true")
    parser.add_argument("--graph-emerging", action="store_true")
    parser.add_argument("--graph-bridges", action="store_true")
    parser.add_argument(
        "--graph-snapshot", choices=("daily", "weekly", "monthly"),
        help="Create an idempotent graph snapshot",
    )
    parser.add_argument("--graph-rebuild", action="store_true")
    parser.add_argument("--graph-validate", action="store_true")
    parser.add_argument("--export-graph-nodes-csv", action="store_true")
    parser.add_argument("--export-graph-edges-csv", action="store_true")
    parser.add_argument("--export-emerging-relationships-csv", action="store_true")
    parser.add_argument("--export-graph-snapshots-csv", action="store_true")
    parser.add_argument("--quality-summary", action="store_true")
    parser.add_argument("--quality-signal", type=positive_int, metavar="SIGNAL_ID")
    parser.add_argument("--quality-sources", action="store_true")
    parser.add_argument("--quality-rules", action="store_true")
    parser.add_argument("--quality-watchlists", action="store_true")
    parser.add_argument("--quality-narratives", action="store_true")
    parser.add_argument("--quality-tokens", action="store_true")
    parser.add_argument("--quality-ai", action="store_true")
    parser.add_argument("--quality-recommendations", action="store_true")
    parser.add_argument("--quality-recalculate", action="store_true")
    parser.add_argument("--quality-validate", action="store_true")
    parser.add_argument(
        "--quality-entity", nargs=2, metavar=("TYPE", "ID"),
        help="Limit quality recalculation to an entity",
    )
    parser.add_argument(
        "--quality-period-days", type=positive_int, default=30,
        help="Quality report and recalculation period (default: 30 days)",
    )
    parser.add_argument(
        "--quality-version", type=positive_int,
        help="Quality calculation version (default: configured version)",
    )
    parser.add_argument("--export-signal-quality-csv", action="store_true")
    parser.add_argument("--export-source-quality-csv", action="store_true")
    parser.add_argument("--export-rule-quality-csv", action="store_true")
    parser.add_argument("--export-watchlist-quality-csv", action="store_true")
    parser.add_argument("--export-ai-quality-csv", action="store_true")
    parser.add_argument("--export-quality-recommendations-csv", action="store_true")
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Start the built-in analytics dashboard",
    )
    parser.add_argument(
        "--dashboard-host",
        default="127.0.0.1",
        help="Dashboard bind address",
    )
    parser.add_argument(
        "--dashboard-port",
        type=int,
        default=8000,
        help="Dashboard port",
    )
    parser.add_argument(
        "--ai-status",
        action="store_true",
        help="Show signal reasoning provider, cache, fallback, and usage status",
    )
    parser.add_argument(
        "--analyze-signal",
        type=positive_int,
        metavar="ID",
        help="Run signal reasoning for a saved signal",
    )
    parser.add_argument(
        "--clear-ai-cache",
        action="store_true",
        help="Clear cached OpenAI signal analyses",
    )
    parser.add_argument(
        "--list-rules",
        action="store_true",
        help="List configured smart alert rules",
    )
    parser.add_argument(
        "--create-rule",
        metavar="NAME",
        help="Create a smart alert rule",
    )
    parser.add_argument(
        "--delete-rule",
        type=positive_int,
        metavar="ID",
        help="Delete a smart alert rule",
    )
    parser.add_argument(
        "--enable-rule",
        type=positive_int,
        metavar="ID",
        help="Enable a smart alert rule",
    )
    parser.add_argument(
        "--disable-rule",
        type=positive_int,
        metavar="ID",
        help="Disable a smart alert rule",
    )
    parser.add_argument(
        "--test-rule",
        type=positive_int,
        metavar="ID",
        help="Test a rule without running its actions",
    )
    parser.add_argument(
        "--rule-condition",
        help="Rule condition as a JSON expression",
    )
    parser.add_argument(
        "--rule-actions",
        help="Comma-separated actions or a JSON action list",
    )
    parser.add_argument(
        "--rule-priority",
        type=int,
        default=0,
        help="Rule priority used for evaluation order",
    )
    parser.add_argument(
        "--rule-disabled",
        action="store_true",
        help="Create a rule in the disabled state",
    )
    parser.add_argument(
        "--signal-json",
        help="Signal facts JSON for --test-rule; defaults to the latest signal",
    )
    parser.add_argument("--list-watchlists", action="store_true")
    parser.add_argument("--create-watchlist", metavar="NAME")
    parser.add_argument("--delete-watchlist", metavar="NAME")
    parser.add_argument("--enable-watchlist", metavar="NAME")
    parser.add_argument("--disable-watchlist", metavar="NAME")
    parser.add_argument(
        "--add-watchlist-token",
        nargs=2,
        metavar=("WATCHLIST", "TOKEN"),
    )
    parser.add_argument(
        "--add-watchlist-narrative",
        nargs=2,
        metavar=("WATCHLIST", "NARRATIVE"),
    )
    parser.add_argument(
        "--remove-watchlist-item",
        nargs=2,
        metavar=("WATCHLIST", "ITEM"),
    )
    parser.add_argument("--watchlist-report", metavar="NAME")
    parser.add_argument("--watchlist-description", default="")
    parser.add_argument("--watchlist-priority", type=int, default=0)
    parser.add_argument("--watchlist-minimum-hype", type=float, default=0)
    parser.add_argument("--watchlist-minimum-momentum", type=float, default=0)
    parser.add_argument("--watchlist-minimum-confidence", type=int, default=0)
    parser.add_argument("--watchlist-no-telegram", action="store_true")
    parser.add_argument("--watchlist-include-digest", action="store_true")
    parser.add_argument("--watchlist-no-highlight", action="store_true")
    parser.add_argument("--watchlist-case-sensitive", action="store_true")
    return parser.parse_args()


def requested_watchlist_command(args: argparse.Namespace) -> bool:
    return bool(
        args.list_watchlists
        or args.create_watchlist
        or args.delete_watchlist
        or args.enable_watchlist
        or args.disable_watchlist
        or args.add_watchlist_token
        or args.add_watchlist_narrative
        or args.remove_watchlist_item
        or args.watchlist_report
    )


def run_watchlist_command(args: argparse.Namespace, db: Database) -> None:
    service = WatchlistService(db)
    if args.list_watchlists:
        watchlists = service.list_watchlists()
        lines = ["Watchlists", ""]
        for watchlist in watchlists:
            report = service.report(watchlist.id)
            lines.append(
                f"{watchlist.name} | "
                f"{'enabled' if watchlist.enabled else 'disabled'} | "
                f"priority {watchlist.priority} | {len(report.items)} items | "
                f"{report.signals_count} signals"
            )
        if not watchlists:
            lines.append("No watchlists configured.")
        logger.info("\n%s", "\n".join(lines))
        return
    if args.create_watchlist:
        watchlist = service.create_watchlist(
            args.create_watchlist,
            args.watchlist_description,
            priority=args.watchlist_priority,
            minimum_hype_score=args.watchlist_minimum_hype,
            minimum_momentum_score=args.watchlist_minimum_momentum,
            minimum_confidence=args.watchlist_minimum_confidence,
            telegram_enabled=not args.watchlist_no_telegram,
            include_in_digest=args.watchlist_include_digest,
            dashboard_highlight=not args.watchlist_no_highlight,
            case_insensitive=not args.watchlist_case_sensitive,
        )
        logger.info("Watchlist created: %d | %s", watchlist.id, watchlist.name)
        return
    if args.delete_watchlist:
        if not service.delete_watchlist(args.delete_watchlist):
            raise WatchlistValidationError(
                f"Watchlist '{args.delete_watchlist}' does not exist"
            )
        logger.info("Watchlist deleted: %s", args.delete_watchlist)
        return
    if args.enable_watchlist or args.disable_watchlist:
        name = args.enable_watchlist or args.disable_watchlist
        try:
            watchlist = service.set_enabled(name, bool(args.enable_watchlist))
        except KeyError as exc:
            raise WatchlistValidationError(
                f"Watchlist '{name}' does not exist"
            ) from exc
        logger.info(
            "Watchlist %s: %s",
            "enabled" if watchlist.enabled else "disabled",
            watchlist.name,
        )
        return
    item_args = args.add_watchlist_token or args.add_watchlist_narrative
    if item_args:
        kind = "token" if args.add_watchlist_token else "narrative"
        try:
            item = service.add_item(item_args[0], kind, item_args[1])
        except KeyError as exc:
            raise WatchlistValidationError(
                f"Watchlist '{item_args[0]}' does not exist"
            ) from exc
        logger.info("Watchlist item added: %s | %s", item.item_type, item.item_value)
        return
    if args.remove_watchlist_item:
        name, item = args.remove_watchlist_item
        try:
            removed = service.remove_item(name, item)
        except KeyError as exc:
            raise WatchlistValidationError(f"Watchlist '{name}' does not exist") from exc
        if not removed:
            raise WatchlistValidationError(f"Item '{item}' was not found in '{name}'")
        logger.info("Watchlist item removed: %s | %s", name, item)
        return
    if args.watchlist_report:
        try:
            report = service.report(args.watchlist_report)
        except KeyError as exc:
            raise WatchlistValidationError(
                f"Watchlist '{args.watchlist_report}' does not exist"
            ) from exc
        logger.info("\n%s", format_watchlist_report(report))


def requested_rule_command(args: argparse.Namespace) -> bool:
    return bool(
        args.list_rules
        or args.create_rule
        or args.delete_rule
        or args.enable_rule
        or args.disable_rule
        or args.test_rule
    )


def run_rule_command(args: argparse.Namespace, db: Database) -> None:
    service = RuleService(db)
    if args.list_rules:
        rules = service.list_rules()
        lines = ["Smart Alert Rules", ""]
        lines.extend(
            f"{rule.id}. {rule.name} | "
            f"{'enabled' if rule.enabled else 'disabled'} | "
            f"priority {rule.priority} | triggers {rule.trigger_count}"
            for rule in rules
        )
        if not rules:
            lines.append("No rules configured.")
        logger.info("\n%s", "\n".join(lines))
        return
    if args.create_rule:
        if not args.rule_condition or not args.rule_actions:
            raise RuleValidationError(
                "--create-rule requires --rule-condition and --rule-actions"
            )
        rule = service.create_rule(
            args.create_rule,
            _json_object(args.rule_condition, "--rule-condition"),
            _rule_actions_argument(args.rule_actions),
            enabled=not args.rule_disabled,
            priority=args.rule_priority,
        )
        logger.info("Rule created: %d | %s", rule.id, rule.name)
        return
    if args.delete_rule:
        if not service.delete_rule(args.delete_rule):
            raise RuleValidationError(f"Rule {args.delete_rule} does not exist")
        logger.info("Rule deleted: %d", args.delete_rule)
        return
    if args.enable_rule or args.disable_rule:
        rule_id = args.enable_rule or args.disable_rule
        try:
            rule = service.set_enabled(rule_id, bool(args.enable_rule))
        except KeyError as exc:
            raise RuleValidationError(f"Rule {rule_id} does not exist") from exc
        logger.info(
            "Rule %s: %d | %s",
            "enabled" if rule.enabled else "disabled",
            rule.id,
            rule.name,
        )
        return
    if args.test_rule:
        facts = (
            SignalFacts.from_mapping(_json_object(args.signal_json, "--signal-json"))
            if args.signal_json
            else _latest_signal_facts(db)
        )
        try:
            result = service.test_rule(args.test_rule, facts)
        except KeyError as exc:
            raise RuleValidationError(f"Rule {args.test_rule} does not exist") from exc
        logger.info(
            "Rule test: %s | %s",
            result.rule.name,
            "MATCH" if result.matched else "NO MATCH",
        )


def _json_object(value: str, label: str) -> dict:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuleValidationError(f"{label} contains invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise RuleValidationError(f"{label} must be a JSON object")
    return parsed


def _rule_actions_argument(value: str) -> list[str]:
    text = value.strip()
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuleValidationError("--rule-actions contains invalid JSON") from exc
        if not isinstance(parsed, list):
            raise RuleValidationError("--rule-actions JSON must be a list")
        return [str(item) for item in parsed]
    return [item.strip() for item in text.split(",") if item.strip()]


def _latest_signal_facts(db: Database) -> SignalFacts:
    rows = db.get_signals(limit=1)
    if not rows:
        raise RuleValidationError(
            "No saved signal is available; provide --signal-json"
        )
    row = rows[0]
    context = db.get_signal_watchlist_context(int(row["id"]))
    return SignalFacts(
        token=row["token"],
        narrative=row["narrative"],
        hype_score=float(row["hype_score"]),
        momentum_score=float(row["momentum_score"]),
        confidence=int(row["confidence"]),
        mentions=int(row["mentions_count"] or 0),
        outcome_success_rate=db.get_entity_outcome_success_rate(
            row["token"],
            row["narrative"],
        ),
        watchlists=tuple(context["names"]),
        watchlist_ids=tuple(context["ids"]),
        watchlist_priority=int(context["highest_priority"]),
        matched_watchlist=bool(context["matched_any"]),
    )


def requested_csv_exports(args: argparse.Namespace) -> tuple[str, ...]:
    kinds = []
    if args.export_signals_csv:
        kinds.append("signals")
    if args.export_outcomes_csv:
        kinds.append("outcomes")
    if args.export_performance_csv:
        kinds.append("performance")
    if args.export_history_csv:
        kinds.append("history")
    if args.export_watchlists_csv:
        kinds.append("watchlists")
    if args.export_watchlist_signals_csv:
        kinds.append("watchlist_signals")
    if args.export_sources_csv:
        kinds.append("sources")
    if args.export_content_items_csv:
        kinds.append("content_items")
    if args.export_unified_events_csv:
        kinds.append("unified_events")
    if args.export_deduplication_csv:
        kinds.append("deduplication")
    if args.export_graph_nodes_csv:
        kinds.append("graph_nodes")
    if args.export_graph_edges_csv:
        kinds.append("graph_edges")
    if args.export_emerging_relationships_csv:
        kinds.append("emerging_relationships")
    if args.export_graph_snapshots_csv:
        kinds.append("graph_snapshots")
    if args.export_signal_quality_csv:
        kinds.append("signal_quality")
    if args.export_source_quality_csv:
        kinds.append("source_quality")
    if args.export_rule_quality_csv:
        kinds.append("rule_quality")
    if args.export_watchlist_quality_csv:
        kinds.append("watchlist_quality")
    if args.export_ai_quality_csv:
        kinds.append("ai_quality")
    if args.export_quality_recommendations_csv:
        kinds.append("quality_recommendations")
    if args.export_csv == "all":
        kinds.extend(("signals", "outcomes", "performance"))
    elif args.export_csv:
        kinds.append(args.export_csv)
    return tuple(dict.fromkeys(kinds))


def run_csv_exports(
    db: Database,
    kinds: tuple[str, ...],
    output_dir: Path,
    from_date: date | None = None,
    to_date: date | None = None,
    history_period: str = "30d",
    history_thresholds: HistoricalThresholds | None = None,
    watchlist_name: str | None = None,
) -> CSVExportResult:
    result = CSVExportService(
        db,
        output_dir,
        history_thresholds=history_thresholds,
    ).export(
        kinds,
        from_date,
        to_date,
        history_period,
        watchlist_name,
    )
    lines = ["CSV export complete", ""]
    labels = (
        ("signals", "Signals exported"),
        ("outcomes", "Outcomes exported"),
        ("performance", "Performance rows exported"),
        ("narrative_performance", "Narrative performance rows exported"),
        ("history_summary", "Historical summaries exported"),
        ("history_timeline", "Historical timeline rows exported"),
        ("narrative_history", "Narrative history rows exported"),
        ("token_history", "Token history rows exported"),
        ("watchlists", "Watchlists exported"),
        ("watchlist_items", "Watchlist items exported"),
        ("watchlist_signals", "Watchlist signals exported"),
        ("signal_quality", "Signal quality rows exported"),
        ("source_quality", "Source quality rows exported"),
        ("rule_quality", "Rule quality rows exported"),
        ("watchlist_quality", "Watchlist quality rows exported"),
        ("ai_quality", "AI quality rows exported"),
        ("quality_recommendations", "Quality recommendations exported"),
    )
    for kind, label in labels:
        if any(item.kind == kind for item in result.files):
            lines.append(f"{label}: {result.count_for(kind)}")
    lines.extend(("", "Files created:"))
    lines.extend(f"* {item.path}" for item in result.files)
    logger.info("\n%s", "\n".join(lines))
    return result


def load_json_list(path: Path, key: str) -> list[str]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Configuration file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Configuration file contains invalid JSON: {path}") from exc
    return [str(item) for item in data.get(key, [])]


def process_posts(
    posts: list[XPost],
    analyzer: Analyzer,
    config: Config,
    db: Database,
    telegram: TelegramAlerter | None,
    event_bus: EventBus | None = None,
    force_mock_ai: bool = False,
) -> None:
    bus = event_bus or build_event_bus(
        db,
        telegram,
        config,
        force_mock_ai=force_mock_ai,
    )
    logger.info("Loaded %d posts", len(posts))
    analyzed_count = 0

    for post in posts:
        if db.has_post(post.id):
            logger.debug("Skipping previously analyzed post %s", post.id)
            continue
        try:
            analysis = analyzer.analyze_post(post.text)
            db.save_analysis(post, analysis)
            db.sync_content_analysis(post.id, analysis)
            for narrative in analysis.narratives:
                bus.publish(
                    NarrativeDetected.create(
                        post_id=post.id,
                        narrative=narrative,
                        importance=analysis.importance,
                    )
                )
            analyzed_count += 1
            logger.info("Analyzed @%s: %s", post.username, analysis.summary)
        except Exception:
            logger.exception("Could not analyze post %s", post.id)

    logger.info("Saved %d new analyses", analyzed_count)
    db.save_narrative_score_history(db.get_recent_signal_stats())
    evaluate_hype(config, db, telegram, analyzer, bus)
    evaluate_signal_outcomes(config, db, bus)
    poll_telegram_performance(telegram, db)


def evaluate_hype(
    config: Config,
    db: Database,
    telegram: TelegramAlerter | None,
    analyzer: Analyzer,
    event_bus: EventBus | None = None,
) -> None:
    momentum_scores = build_momentum_scores(db)
    db.save_daily_momentum(momentum_scores)
    candidates = []
    for row in db.get_recent_signal_stats():
        signal = build_hype_signal(row)
        logger.info(
            "Hype score | %s:%s | %d/100 %s",
            signal.kind,
            signal.name,
            signal.display_hype_score,
            signal.hype_label,
        )
        logger.debug(
            "Raw hype score | %s:%s | %.2f (%d mentions, %.2f avg importance)",
            signal.kind,
            signal.name,
            signal.hype_score,
            signal.mentions_count,
            signal.average_importance,
        )
        if signal.hype_score < config.hype_alert_threshold:
            continue
        if db.alert_recently_sent(signal.kind, signal.name):
            continue

        context_rows = db.get_signal_posts(signal.kind, signal.name, limit=100)
        candidates.append(
            EnrichedCandidate(
                candidate=HypeCandidate(
                    signal=signal,
                    post_ids=frozenset(str(item["post_id"]) for item in context_rows),
                ),
                rows=context_rows,
            )
        )

    for primary, merged in merge_alert_candidates(candidates):
        send_candidate_alert(
            primary,
            merged,
            momentum_scores,
            analyzer,
            telegram,
            db,
            event_bus,
        )


def merge_alert_candidates(
    candidates: list[EnrichedCandidate],
) -> list[tuple[EnrichedCandidate, EnrichedCandidate | None]]:
    remaining = sorted(
        candidates,
        key=lambda item: item.candidate.signal.hype_score,
        reverse=True,
    )
    groups = []
    while remaining:
        primary = remaining.pop(0)
        matches = [
            item
            for item in remaining
            if should_merge_candidates(primary.candidate, item.candidate)
        ]
        merged = max(
            matches,
            key=lambda item: candidate_overlap(primary.candidate, item.candidate),
            default=None,
        )
        if merged is not None:
            remaining.remove(merged)
        groups.append((primary, merged))
    return groups


def send_candidate_alert(
    primary: EnrichedCandidate,
    merged: EnrichedCandidate | None,
    momentum_scores: list[NarrativeMomentum],
    analyzer: Analyzer,
    telegram: TelegramAlerter | None,
    db: Database,
    event_bus: EventBus | None = None,
) -> None:
    signal = primary.candidate.signal
    merged_signal = merged.candidate.signal if merged else None
    unique_rows = {
        str(item["post_id"]): item
        for item in primary.rows
    }
    if merged:
        for item in merged.rows:
            unique_rows.setdefault(str(item["post_id"]), item)
    context_rows = list(unique_rows.values())
    context_rows.sort(key=lambda item: int(item["importance"]), reverse=True)
    merged_hype_score = None
    baseline_mentions_count = signal.mentions_count
    if merged_signal and context_rows:
        unique_mentions_count = len(context_rows)
        baseline_mentions_count = unique_mentions_count
        average_importance = sum(
            float(item["importance"]) for item in context_rows
        ) / unique_mentions_count
        merged_hype_score = round(unique_mentions_count * average_importance, 2)
    context_rows = context_rows[:3]
    top_posts = [
        AlertPost(username=str(item["username"]), text=str(item["text"]))
        for item in context_rows
    ]
    related_tokens = sorted(
        {
            str(token)
            for item in context_rows
            for token in json.loads(item["tokens_json"])
        }
    )
    related_narratives = sorted(
        {
            str(narrative)
            for item in context_rows
            for narrative in json.loads(item["narratives_json"])
        }
    )
    post_prompts = [f"@{post.username}: {post.text}" for post in top_posts]
    combined_kind = "token + narrative" if merged_signal else signal.kind
    combined_name = (
        f"{signal.name} + {merged_signal.name}" if merged_signal else signal.name
    )
    combined_hype = merged_hype_score if merged_hype_score is not None else signal.hype_score
    display_hype_for_explanation = normalize_hype_score(combined_hype)
    try:
        insight = analyzer.explain_spike(
            combined_kind,
            combined_name,
            display_hype_for_explanation,
            post_prompts,
            related_tokens,
            related_narratives,
        )
    except Exception:
        logger.exception("Could not generate spike explanation; using local fallback")
        insight = LocalAnalyzer([]).explain_spike(
            combined_kind,
            combined_name,
            display_hype_for_explanation,
            post_prompts,
            related_tokens,
            related_narratives,
        )
    relevant_momentum = [
        item
        for item in momentum_scores
        if item.name in related_narratives
        or (signal.kind == "narrative" and item.name == signal.name)
        or (
            merged_signal is not None
            and merged_signal.kind == "narrative"
            and item.name == merged_signal.name
        )
    ]
    alert = HypeAlert(
        signal=signal,
        insight=insight,
        top_posts=top_posts,
        related_tokens=related_tokens,
        related_narratives=related_narratives,
        momentum=(relevant_momentum or momentum_scores)[:5],
        merged_signal=merged_signal,
        merged_hype_score=merged_hype_score,
        baseline_mentions_count=baseline_mentions_count,
    )

    logger.warning("\n%s", format_hype_alert(alert))
    bus = event_bus or build_event_bus(db, telegram)
    bus.publish(SignalCreated.from_alert(alert))


def build_signal_history_record(alert: HypeAlert) -> dict:
    return SignalCreated.from_alert(alert).history_record()


def build_event_bus(
    db: Database,
    telegram: TelegramAlerter | None = None,
    config: Config | None = None,
    *,
    force_mock_ai: bool = False,
) -> EventBus:
    event_bus = EventBus()
    reasoning = (
        create_signal_reasoning_service(
            db,
            config,
            event_bus=event_bus,
            force_mock=force_mock_ai,
        )
        if config is not None
        else None
    )
    register_default_subscribers(event_bus, db, telegram, reasoning, config)
    return event_bus


def build_telegram(config: Config, disabled: bool = False) -> TelegramAlerter | None:
    if disabled:
        logger.info("Telegram disabled by --no-telegram")
        return None
    if config.telegram_bot_token and config.telegram_chat_id:
        return TelegramAlerter(config.telegram_bot_token, config.telegram_chat_id)
    logger.info("Telegram not configured")
    return None


def build_summary(db: Database) -> NarrativeSummary:
    token_items = []
    narrative_items = []
    for row in db.get_recent_signal_stats():
        signal = build_hype_signal(row)
        item = SummaryItem(name=signal.name, hype_score=signal.hype_score)
        if signal.kind == "token":
            token_items.append(item)
        else:
            narrative_items.append(item)

    token_items.sort(key=lambda item: item.hype_score, reverse=True)
    narrative_items.sort(key=lambda item: item.hype_score, reverse=True)
    important_posts = [
        AlertPost(username=str(row["username"]), text=str(row["text"]))
        for row in db.get_most_important_posts()
    ]
    return NarrativeSummary(
        top_tokens=token_items[:3],
        top_narratives=narrative_items[:3],
        important_posts=important_posts,
    )


def print_and_send_summary(db: Database, telegram: TelegramAlerter | None) -> None:
    summary = build_summary(db)
    logger.info("\n%s", format_summary(summary))
    if telegram:
        try:
            telegram.send_summary(summary)
            logger.info("Telegram summary sent")
        except Exception:
            logger.exception("Telegram summary failed")


def build_trend_report(db: Database) -> TrendReport:
    top_24h = [
        NarrativeTrend(name=str(row["narrative"]), score=float(row["score"]))
        for row in db.get_top_narrative_history(24)
    ]
    top_7d = [
        NarrativeTrend(name=str(row["narrative"]), score=float(row["score"]))
        for row in db.get_top_narrative_history(24 * 7)
    ]
    fastest_growing = [
        NarrativeGrowth(
            name=str(row["narrative"]),
            growth_percent=float(row["growth_percent"]),
        )
        for row in db.get_fastest_growing_narratives()
    ]
    return TrendReport(
        top_24h=top_24h,
        top_7d=top_7d,
        fastest_growing=fastest_growing,
        momentum=build_momentum_scores(db)[:5],
    )


def print_and_send_trend_report(
    db: Database,
    telegram: TelegramAlerter | None,
) -> None:
    report = build_trend_report(db)
    logger.info("\n%s", format_trend_report(report))
    if telegram:
        try:
            telegram.send_trend_report(report)
            logger.info("Telegram trend report sent")
        except Exception:
            logger.exception("Telegram trend report failed")


def build_daily_digest(db: Database) -> DailyDigest:
    token_items = []
    narrative_items = []
    for row in db.get_signal_stats_for_hours(24):
        signal = build_hype_signal(row)
        item = SummaryItem(name=signal.name, hype_score=signal.hype_score)
        if signal.kind == "token":
            token_items.append(item)
        else:
            narrative_items.append(item)
    token_items.sort(key=lambda item: item.hype_score, reverse=True)
    narrative_items.sort(key=lambda item: item.hype_score, reverse=True)

    growth_rows = db.get_fastest_growing_narratives(limit=1)
    fastest_growing = None
    if growth_rows:
        fastest_growing = NarrativeGrowth(
            name=str(growth_rows[0]["narrative"]),
            growth_percent=float(growth_rows[0]["growth_percent"]),
        )
    important_posts = [
        AlertPost(username=str(row["username"]), text=str(row["text"]))
        for row in db.get_most_important_posts(lookback_minutes=24 * 60, limit=3)
    ]

    top_token = token_items[0].name if token_items else "no token"
    top_narrative = narrative_items[0].name if narrative_items else "no narrative"
    if fastest_growing:
        growth_text = (
            f"{fastest_growing.name} is the fastest-growing narrative "
            f"at {fastest_growing.growth_percent:+.0f}%."
        )
    else:
        growth_text = "There is not enough history to identify narrative growth."
    final_summary = (
        f"{top_token} led token attention while {top_narrative} led narratives. "
        f"{growth_text}"
    )
    digest_signals = db.get_rule_flagged_signals("include_in_digest", limit=5)
    watchlist_digest_signals = db.get_watchlist_digest_signals(limit=5)
    combined_digest_signals = [*digest_signals, *watchlist_digest_signals]
    if combined_digest_signals:
        digest_names = list(
            dict.fromkeys(
                str(row["token"] or row["narrative"] or "Unknown")
                for row in combined_digest_signals
            )
        )
        final_summary += " Focused signals: " + ", ".join(digest_names) + "."
    return DailyDigest(
        top_tokens=token_items[:5],
        top_narratives=narrative_items[:5],
        fastest_growing=fastest_growing,
        important_posts=important_posts,
        final_summary=final_summary,
        momentum=build_momentum_scores(db)[:5],
    )


def build_momentum_scores(db: Database) -> list[NarrativeMomentum]:
    growth = {
        str(row["narrative"]): float(row["growth_percent"])
        for row in db.get_fastest_growing_narratives(limit=100)
    }
    scores = [
        NarrativeMomentum(
            name=str(row["narrative"]),
            score=calculate_momentum_score(
                mentions_count=int(row["mentions_count"]),
                average_importance=float(row["average_importance"]),
                growth_percent=growth.get(str(row["narrative"]), 0.0),
                recency_hours=float(row["recency_hours"] or 0.0),
            ),
        )
        for row in db.get_narrative_momentum_inputs()
    ]
    return sorted(scores, key=lambda item: item.score, reverse=True)


def print_and_send_daily_digest(
    db: Database,
    telegram: TelegramAlerter | None,
) -> None:
    digest = build_daily_digest(db)
    logger.info("\n%s", format_daily_digest(digest))
    if telegram:
        try:
            telegram.send_daily_digest(digest)
            logger.info("Telegram daily digest sent")
        except Exception:
            logger.exception("Telegram daily digest failed")


def build_history_report(db: Database) -> MomentumHistoryReport:
    items = []
    for row in db.get_momentum_history_report():
        today = int(row["today_score"])
        previous = row["seven_days_ago_score"]
        previous_score = int(previous) if previous is not None else None
        if previous_score is None:
            change_percent = None
        elif previous_score > 0:
            change_percent = ((today - previous_score) / previous_score) * 100.0
        elif today > 0:
            change_percent = 100.0
        else:
            change_percent = 0.0
        items.append(
            MomentumHistoryItem(
                name=str(row["narrative"]),
                seven_days_ago=previous_score,
                today=today,
                change_percent=change_percent,
            )
        )
    return MomentumHistoryReport(items=items)


def print_and_send_history_report(
    db: Database,
    telegram: TelegramAlerter | None,
) -> None:
    db.save_daily_momentum(build_momentum_scores(db))
    report = build_history_report(db)
    logger.info("\n%s", format_history_report(report))
    if telegram:
        try:
            telegram.send_history_report(report)
            logger.info("Telegram history report sent")
        except Exception:
            logger.exception("Telegram history report failed")


def build_historical_analytics(
    config: Config,
    db: Database,
    period: str,
):
    return HistoricalAnalyticsService(
        db,
        HistoricalThresholds(
            growth_percent=config.history_growth_threshold,
            minimum_activity=config.history_minimum_activity,
        ),
    ).build_report(period)


def print_historical_analytics(config: Config, db: Database, period: str) -> None:
    report = build_historical_analytics(config, db, period)
    logger.info("\n%s", format_historical_report(report))


def build_opportunity_report(db: Database, limit: int = 10) -> OpportunityReport:
    opportunities = []
    for row in db.get_opportunity_inputs():
        momentum_score = int(row["momentum_score"])
        previous = row["seven_days_ago_score"]
        if previous is None:
            growth_percent = None
        elif int(previous) > 0:
            growth_percent = ((momentum_score - int(previous)) / int(previous)) * 100.0
        elif momentum_score > 0:
            growth_percent = 100.0
        else:
            growth_percent = 0.0
        opportunities.append(
            build_opportunity(
                name=str(row["narrative"]),
                momentum_score=momentum_score,
                growth_percent=growth_percent,
                recency_days=float(row["recency_days"] or 0.0),
            )
        )
    opportunities.sort(key=lambda item: item.rank_score, reverse=True)
    return OpportunityReport(opportunities=opportunities[:limit])


def print_and_send_opportunity_report(
    db: Database,
    telegram: TelegramAlerter | None,
) -> None:
    db.save_daily_momentum(build_momentum_scores(db))
    report = build_opportunity_report(db)
    logger.info("\n%s", format_opportunity_report(report))
    if telegram:
        try:
            telegram.send_opportunity_report(report)
            logger.info("Telegram opportunity report sent")
        except Exception:
            logger.exception("Telegram opportunity report failed")


def build_performance_report(db: Database) -> SignalPerformanceReport:
    summary = db.get_signal_performance_summary()
    signals_generated = int(summary["signals_generated"] or 0)
    successful = int(summary["successful"] or 0)
    accuracy = (successful / signals_generated * 100) if signals_generated else 0.0
    best = [
        PerformanceNarrative(
            name=str(row["name"]),
            signals_count=int(row["signals_count"]),
            average_momentum=float(row["average_momentum"] or 0.0),
            average_confidence=float(row["average_confidence"] or 0.0),
        )
        for row in db.get_signal_performance_narratives("DESC")
    ]
    worst = [
        PerformanceNarrative(
            name=str(row["name"]),
            signals_count=int(row["signals_count"]),
            average_momentum=float(row["average_momentum"] or 0.0),
            average_confidence=float(row["average_confidence"] or 0.0),
        )
        for row in db.get_signal_performance_narratives("ASC")
    ]
    return SignalPerformanceReport(
        signals_generated=signals_generated,
        successful=successful,
        accuracy=accuracy,
        average_confidence=float(summary["average_confidence"] or 0.0),
        average_momentum=float(summary["average_momentum"] or 0.0),
        best_narratives=best,
        worst_narratives=worst,
    )


def print_and_send_performance_report(
    db: Database,
    telegram: TelegramAlerter | None,
) -> None:
    report = build_performance_report(db)
    logger.info("\n%s", format_performance_report(report))
    if telegram:
        try:
            telegram.send_performance_report(report)
            logger.info("Telegram performance report sent")
        except Exception:
            logger.exception("Telegram performance report failed")


def outcome_thresholds(config: Config) -> OutcomeThresholds:
    return OutcomeThresholds(
        success=config.outcome_success_threshold,
        failure=config.outcome_failure_threshold,
    )


def evaluate_signal_outcomes(
    config: Config,
    db: Database,
    event_bus: EventBus | None = None,
) -> int:
    evaluated = OutcomeEvaluator(
        db,
        thresholds=outcome_thresholds(config),
        event_bus=event_bus,
    ).evaluate_due(config.outcome_evaluation_windows)
    if evaluated:
        logger.info("Evaluated %d signal outcomes", evaluated)
    return evaluated


def build_outcome_report(
    db: Database,
    period_hours: int | None = None,
) -> SignalOutcomeReport:
    summary = db.get_signal_outcome_summary(period_hours=period_hours)
    signals_evaluated = int(summary["signals_evaluated"] or 0)
    success = int(summary["success"] or 0)

    def narrative_rows(order: str) -> list[OutcomeNarrative]:
        return [
            OutcomeNarrative(
                name=str(row["name"]),
                evaluated_count=int(row["evaluated_count"]),
                outcome_score=float(row["outcome_score"] or 0.0),
                average_momentum_change=float(
                    row["average_momentum_change"] or 0.0
                ),
            )
            for row in db.get_signal_outcome_narratives(
                order,
                period_hours=period_hours,
            )
        ]

    return SignalOutcomeReport(
        signals_evaluated=signals_evaluated,
        success=success,
        neutral=int(summary["neutral"] or 0),
        failed=int(summary["failed"] or 0),
        success_rate=(success / signals_evaluated * 100) if signals_evaluated else 0.0,
        average_mention_change=float(summary["average_mention_change"] or 0.0),
        average_momentum_change=float(summary["average_momentum_change"] or 0.0),
        best_narratives=narrative_rows("DESC"),
        worst_narratives=narrative_rows("ASC"),
        average_hype_change=float(summary["average_hype_change"] or 0.0),
    )


def print_and_send_outcome_report(
    config: Config,
    db: Database,
    telegram: TelegramAlerter | None,
    period_hours: int | None = None,
) -> None:
    evaluate_signal_outcomes(config, db, build_event_bus(db, telegram))
    report = build_outcome_report(db, period_hours)
    logger.info("\n%s", format_outcome_report(report))
    if telegram:
        try:
            telegram.send_outcome_report(report)
            logger.info("Telegram outcome report sent")
        except Exception:
            logger.exception("Telegram outcome report failed")


def poll_telegram_performance(
    telegram: TelegramAlerter | None,
    db: Database,
) -> None:
    if not telegram:
        return
    try:
        handled = telegram.poll_commands(
            build_outcome_report(db),
            WatchlistService(db),
        )
        if handled:
            logger.info("Handled %d Telegram command(s)", handled)
    except Exception:
        logger.exception("Telegram command polling failed")


def run_local(
    config: Config,
    db: Database,
    no_telegram: bool = False,
    show_summary: bool = False,
) -> None:
    narratives = load_json_list(config.narratives_path, "narratives")
    posts = load_sample_posts(config.sample_posts_path)
    telegram = build_telegram(config, no_telegram)
    process_posts(
        posts,
        LocalAnalyzer(narratives),
        config,
        db,
        telegram,
    )
    if show_summary:
        print_and_send_summary(db, telegram)


def build_analyzer(
    config: Config,
    narratives: list[str],
    mock_ai: bool = False,
) -> Analyzer:
    use_mock = mock_ai or config.ai_provider == "mock"
    if config.ai_provider == "auto" and not config.openai_api_key:
        use_mock = True
    if not config.openai_api_key and config.openai_fallback_to_mock:
        use_mock = True
    if use_mock:
        logger.info("Mock AI enabled; OpenAI API will not be used")
        return LocalAnalyzer(narratives)
    if not config.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required unless --mock-ai is enabled")
    return OpenAIAnalyzer(config.openai_api_key, config.openai_model, narratives)


def validate_live_config(config: Config, mock_ai: bool = False) -> None:
    if not config.x_bearer_token:
        raise RuntimeError("X_BEARER_TOKEN is required in live mode")
    if (
        not mock_ai
        and config.ai_provider == "openai"
        and not config.openai_api_key
        and not config.openai_fallback_to_mock
    ):
        raise RuntimeError("OPENAI_API_KEY is required in live mode")


def validate_rss_config(config: Config, mock_ai: bool = False) -> None:
    if (
        not mock_ai
        and config.ai_provider == "openai"
        and not config.openai_api_key
        and not config.openai_fallback_to_mock
    ):
        raise RuntimeError("OPENAI_API_KEY is required in RSS mode")


def run_live_once(
    config: Config,
    db: Database,
    no_telegram: bool = False,
    mock_ai: bool = False,
) -> None:
    accounts = [name.lstrip("@") for name in load_json_list(config.accounts_path, "accounts")]
    narratives = load_json_list(config.narratives_path, "narratives")
    posts = XClient(config.x_bearer_token).fetch_recent_posts(accounts, config.posts_per_account)
    analyzer = build_analyzer(config, narratives, mock_ai)
    process_posts(
        posts,
        analyzer,
        config,
        db,
        build_telegram(config, no_telegram),
        force_mock_ai=mock_ai,
    )


def run_live(
    config: Config,
    db: Database,
    no_telegram: bool = False,
    mock_ai: bool = False,
) -> None:
    validate_live_config(config, mock_ai)
    while True:
        started_at = time.time()
        try:
            run_live_once(config, db, no_telegram, mock_ai)
        except Exception:
            logger.exception("Live run failed")

        elapsed = time.time() - started_at
        sleep_seconds = max(0, config.fetch_interval_seconds - elapsed)
        logger.info("Sleeping for %.0f seconds", sleep_seconds)
        time.sleep(sleep_seconds)


def run_rss_once(
    config: Config,
    db: Database,
    no_telegram: bool = False,
    mock_ai: bool = False,
) -> None:
    narratives = load_json_list(config.narratives_path, "narratives")
    analyzer = build_analyzer(config, narratives, mock_ai)
    telegram = build_telegram(config, no_telegram)
    event_bus = build_event_bus(
        db,
        telegram,
        config,
        force_mock_ai=mock_ai,
    )
    from app.ingestion.service import MultiSourceIngestionService

    ingestion = MultiSourceIngestionService(db, config, event_bus)
    result = ingestion.fetch_all()
    posts = list(result.posts)
    event_bus.publish(
        RSSFetched.create(posts, len(db.get_content_sources(enabled=True)))
    )
    process_posts(
        posts,
        analyzer,
        config,
        db,
        telegram,
        event_bus,
        force_mock_ai=mock_ai,
    )


def run_rss(
    config: Config,
    db: Database,
    no_telegram: bool = False,
    mock_ai: bool = False,
    watch: bool = False,
) -> None:
    validate_rss_config(config, mock_ai)
    if not watch:
        run_rss_once(config, db, no_telegram, mock_ai)
        return
    while True:
        started_at = time.time()
        try:
            run_rss_once(config, db, no_telegram, mock_ai)
        except Exception:
            logger.exception("RSS run failed")

        elapsed = time.time() - started_at
        sleep_seconds = max(0, config.fetch_interval_seconds - elapsed)
        logger.info("Sleeping for %.0f seconds", sleep_seconds)
        time.sleep(sleep_seconds)


def run_dashboard(config: Config, host: str, port: int) -> None:
    import uvicorn

    from app.dashboard import create_app

    logger.info("Dashboard available at http://%s:%d", host, port)
    uvicorn.run(create_app(config.database_path, config=config), host=host, port=port)


def requested_graph_command(args: argparse.Namespace) -> bool:
    return any(
        (
            args.graph_summary,
            args.graph_node,
            args.graph_top_narratives,
            args.graph_top_tokens,
            args.graph_emerging,
            args.graph_bridges,
            args.graph_snapshot,
            args.graph_rebuild,
            args.graph_validate,
        )
    )


def run_graph_command(args: argparse.Namespace, config: Config, db: Database) -> None:
    from app.graph.service import GraphService

    service = GraphService(db, config)
    if args.graph_rebuild:
        counts = service.rebuild()
        logger.info(
            "Graph rebuild complete: %d nodes, %d edges, %d events and %d signals processed",
            counts["nodes"], counts["edges"], counts["events_processed"],
            counts["signals_processed"],
        )
    elif args.graph_validate:
        issues = service.validate()
        if issues:
            logger.warning("Graph validation found %d issue(s)\n%s", len(issues), json.dumps(issues, indent=2))
        else:
            logger.info("Graph validation complete: no issues found")
    elif args.graph_snapshot:
        snapshot, created = service.create_snapshot(args.graph_snapshot)
        logger.info(
            "Graph snapshot %s: id=%d nodes=%d edges=%d",
            "created" if created else "already exists",
            snapshot.id, snapshot.node_count, snapshot.edge_count,
        )
    elif args.graph_node:
        detail = service.node_detail(args.graph_node[0], args.graph_node[1])
        if detail is None:
            raise ValueError("Graph node not found")
        logger.info("Graph node\n%s", json.dumps(detail, indent=2, default=str))
    elif args.graph_top_narratives or args.graph_top_tokens:
        node_type = "narrative" if args.graph_top_narratives else "token"
        rows = service.top_nodes(node_type)
        text = "\n".join(
            f"{index}. {row['label']} - weighted degree {row['weighted_degree']:.2f}"
            for index, row in enumerate(rows, 1)
        ) or "None"
        logger.info("Top graph %ss\n%s", node_type, text)
    elif args.graph_emerging:
        logger.info(
            "Emerging relationships\n%s",
            "\n".join(
                f"{item['source_label']} <-> {item['target_label']} - "
                f"{item['emerging_relationship_score']:.1f} ({item['classification']})"
                for item in service.emerging()
            ) or "None",
        )
    elif args.graph_bridges:
        logger.info(
            "Bridge nodes\n%s",
            "\n".join(
                f"{item['label']} - {item['bridge_score']:.1f}"
                for item in service.bridges()
            ) or "None",
        )
    else:
        logger.info("\n%s", service.format_summary())


def requested_quality_command(args: argparse.Namespace) -> bool:
    return any((
        args.quality_summary, args.quality_signal, args.quality_sources,
        args.quality_rules, args.quality_watchlists, args.quality_narratives,
        args.quality_tokens, args.quality_ai, args.quality_recommendations,
        args.quality_recalculate, args.quality_validate,
    ))


def run_quality_command(args: argparse.Namespace, config: Config, db: Database) -> None:
    from app.quality import SignalQualityService, format_quality_summary

    service = SignalQualityService(db, config)
    period = args.quality_period_days
    version = args.quality_version
    if args.quality_signal:
        result = service.calculate_signal(args.quality_signal, version=version)
        logger.info("Signal quality\n%s", json.dumps(result.as_dict(), indent=2))
    elif args.quality_recalculate:
        entity_type, entity_id = args.quality_entity or (None, None)
        result = service.recalculate(
            entity_type=entity_type, entity_id=entity_id,
            period_days=period, version=version,
        )
        logger.info("Quality recalculation complete: %s", json.dumps(result))
    elif args.quality_validate:
        issues = service.validate()
        logger.info(
            "Quality validation: %s\n%s",
            "no issues found" if not issues else f"{len(issues)} issue(s)",
            json.dumps(issues, indent=2),
        )
    elif args.quality_recommendations:
        rows = service.generate_recommendations(period)
        logger.info("Quality recommendations\n%s", json.dumps(rows, indent=2))
    elif args.quality_ai:
        service.calculate_missing(version=version)
        logger.info("AI quality\n%s", json.dumps(service.ai_report(period), indent=2))
    elif any((args.quality_sources, args.quality_rules, args.quality_watchlists,
              args.quality_narratives, args.quality_tokens)):
        entity_type = (
            "source" if args.quality_sources else "rule" if args.quality_rules
            else "watchlist" if args.quality_watchlists else "narrative"
            if args.quality_narratives else "token"
        )
        service.calculate_missing(version=version)
        logger.info(
            "%s quality\n%s", entity_type.title(),
            json.dumps(service.entity_report(entity_type, period_days=period), indent=2),
        )
    else:
        logger.info("\n%s", format_quality_summary(service.summary(period)))


def requested_ingestion_command(args: argparse.Namespace) -> bool:
    return any(
        (
            args.list_sources,
            args.source_status,
            args.fetch_source,
            args.enable_source,
            args.disable_source,
            args.list_unified_events,
            args.show_unified_event,
            args.deduplication_report,
            args.rebuild_unified_events,
        )
    )


def run_ingestion_command(
    args: argparse.Namespace,
    config: Config,
    db: Database,
) -> None:
    from app.ingestion.service import MultiSourceIngestionService, format_deduplication_report

    service = MultiSourceIngestionService(db, config)
    service.sync_configured_sources()
    if args.enable_source or args.disable_source:
        identifier = args.enable_source or args.disable_source
        enabled = bool(args.enable_source)
        if not db.update_content_source(identifier, enabled=enabled):
            raise ValueError(f"Content source not found: {identifier}")
        logger.info("Source %s %s", identifier, "enabled" if enabled else "disabled")
    elif args.fetch_source:
        result = service.fetch_source(args.fetch_source)
        logger.info(
            "Source fetch complete: %d fetched, %d accepted, %d duplicates, %d new events",
            result.fetched_count, result.accepted_count, result.duplicate_count,
            result.new_event_count,
        )
    elif args.list_sources or args.source_status:
        for row in db.get_content_sources():
            logger.info(
                "%s | %s | %s | priority=%s | failures=%s | success=%.1f%%",
                row["source_key"], "enabled" if row["enabled"] else "disabled",
                row["source_type"], row["priority"], row["consecutive_failures"],
                float(row["success_rate"]),
            )
    elif args.list_unified_events:
        for row in db.get_unified_events(limit=100):
            logger.info(
                "%s | %s | sources=%s items=%s hype=%.1f momentum=%.1f",
                row["id"], row["title"], row["source_count"], row["item_count"],
                float(row["hype_score"]), float(row["momentum_score"]),
            )
    elif args.show_unified_event:
        event = db.get_unified_event(args.show_unified_event)
        if event is None:
            raise ValueError(f"Unified event not found: {args.show_unified_event}")
        payload = {
            "event": dict(event),
            "items": [dict(row) for row in db.get_unified_event_items(event["id"])],
            "history": [dict(row) for row in db.get_unified_event_history(event["id"])],
        }
        logger.info("Unified event\n%s", json.dumps(payload, indent=2, default=str))
    elif args.deduplication_report:
        logger.info("\n%s", format_deduplication_report(db))
    elif args.rebuild_unified_events:
        processed, created = service.events.rebuild()
        logger.info(
            "Unified event rebuild complete: %d content items checked, %d events created",
            processed, created,
        )


def main() -> None:
    configure_logging()
    args = parse_args()

    try:
        config = load_config()
    except Exception:
        logger.exception("Startup failed")
        raise SystemExit(1)

    if args.dashboard:
        try:
            run_dashboard(config, args.dashboard_host, args.dashboard_port)
        except KeyboardInterrupt:
            logger.info("Stopped")
        except Exception:
            logger.exception("Dashboard failed")
            raise SystemExit(1)
        return

    try:
        db = Database(config.database_path)
        db.initialize()
        if args.reset_db:
            db.reset()
            logger.info("Database reset complete")
    except Exception:
        logger.exception("Startup failed")
        raise SystemExit(1)

    try:
        if args.clear_ai_cache:
            removed = db.clear_ai_cache()
            logger.info("AI cache cleared: %d entr%s", removed, "y" if removed == 1 else "ies")
            return
        if args.ai_status:
            status = create_signal_reasoning_service(db, config).status()
            logger.info("AI status\n%s", json.dumps(status, indent=2, sort_keys=True))
            return
        if args.analyze_signal:
            ai_bus = EventBus()
            ai_bus.subscribe(AIAnalysisCompleted, AIRuleEvaluationSubscriber(db, None))
            result = create_signal_reasoning_service(
                db,
                config,
                event_bus=ai_bus,
            ).analyze_signal(
                args.analyze_signal,
                force=True,
            )
            logger.info(
                "Signal AI analysis\n%s",
                result.model_dump_json(indent=2),
            )
            return
        if requested_watchlist_command(args):
            run_watchlist_command(args, db)
            return
        if requested_rule_command(args):
            run_rule_command(args, db)
            return
        if requested_ingestion_command(args):
            run_ingestion_command(args, config, db)
            return
        if requested_graph_command(args):
            run_graph_command(args, config, db)
            return
        if requested_quality_command(args):
            run_quality_command(args, config, db)
            return
        export_kinds = requested_csv_exports(args)
        if export_kinds:
            run_csv_exports(
                db,
                export_kinds,
                args.output_dir,
                args.from_date,
                args.to_date,
                args.period,
                HistoricalThresholds(
                    growth_percent=config.history_growth_threshold,
                    minimum_activity=config.history_minimum_activity,
                ),
                args.export_watchlist_signals_csv,
            )
        elif args.top_opportunities:
            print_and_send_opportunity_report(db, build_telegram(config, args.no_telegram))
        elif args.performance_report:
            print_and_send_performance_report(db, build_telegram(config, args.no_telegram))
        elif args.evaluate_signals:
            evaluated = evaluate_signal_outcomes(config, db, build_event_bus(db))
            logger.info("Signal evaluation complete: %d outcome(s) saved", evaluated)
        elif args.outcome_report:
            print_and_send_outcome_report(
                config,
                db,
                build_telegram(config, args.no_telegram),
                args.outcome_period_hours,
            )
        elif args.history_report:
            print_historical_analytics(config, db, args.period)
        elif args.daily_digest:
            print_and_send_daily_digest(db, build_telegram(config, args.no_telegram))
        elif args.trend_report:
            print_and_send_trend_report(db, build_telegram(config, args.no_telegram))
        elif args.mode == "local":
            run_local(config, db, args.no_telegram, args.summary)
        elif args.mode == "rss":
            run_rss(config, db, args.no_telegram, args.mock_ai, args.watch)
        else:
            run_live(config, db, args.no_telegram, args.mock_ai)
    except KeyboardInterrupt:
        logger.info("Stopped")
    except Exception:
        logger.exception("%s mode failed", args.mode.capitalize())
        raise SystemExit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
