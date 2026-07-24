import csv
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.ai.analyzer import SpikeInsight
from app.alerts.telegram import HypeAlert, format_telegram_hype_alert
from app.dashboard.app import create_app
from app.db.database import Database
from app.events import EventBus, SignalCreated, WatchlistMatched
from app.events.subscribers import register_default_subscribers
from app.export.csv_exporter import CSVExportService
from app.main import parse_args
from app.rules import RuleService, SignalFacts, evaluate_condition
from app.scoring.hype_score import HypeSignal
from app.watchlists import WatchlistService


def signal_event(
    token: str = "SOL",
    narrative: str = "Solana ecosystem",
    hype: float = 88,
    momentum: float = 76,
    confidence: int = 8,
) -> SignalCreated:
    alert = HypeAlert(
        signal=HypeSignal("token", token, 4, 9, hype),
        insight=SpikeInsight("Focused signal", "research", confidence),
        top_posts=[],
        related_tokens=[token],
        related_narratives=[narrative],
        momentum=[],
        baseline_mentions_count=4,
    )
    return SignalCreated(
        alert=alert,
        signal_type="token + narrative",
        token=token,
        narrative=narrative,
        hype_score=hype,
        momentum_score=momentum,
        confidence=confidence,
        action="research",
        mentions_count=4,
    )


class FakeTelegram:
    def __init__(self) -> None:
        self.alerts = []

    def send_hype_alert(self, alert, watchlist_names=()) -> None:
        self.alerts.append((alert, watchlist_names))

    def send_rule_alert(self, *args) -> None:
        pass


class WatchlistTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db = Database(self.root / "watchlists.sqlite3")
        self.db.initialize()
        self.service = WatchlistService(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self.temp_dir.cleanup()

    def test_crud_normalization_duplicates_and_removal(self) -> None:
        watchlist = self.service.create_watchlist("Main Portfolio", priority=8)
        token = self.service.add_item(watchlist.id, "token", "$sol")
        narrative = self.service.add_item(
            watchlist.id,
            "narrative",
            "  Solana   ecosystem ",
        )

        self.assertEqual(token.item_value, "SOL")
        self.assertEqual(narrative.item_value, "Solana ecosystem")
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.create_watchlist("main portfolio")
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.add_item(watchlist.id, "token", "SOL")
        self.assertTrue(self.service.remove_item(watchlist.id, "sol"))
        self.assertEqual(len(self.service.list_items(watchlist.id)), 1)

    def test_matching_is_exact_enabled_and_threshold_aware(self) -> None:
        first = self.service.create_watchlist(
            "Focused",
            minimum_hype_score=80,
            minimum_momentum_score=70,
            minimum_confidence=8,
        )
        second = self.service.create_watchlist("Also SOL", priority=9)
        disabled = self.service.create_watchlist("Disabled", enabled=False)
        for watchlist in (first, second, disabled):
            self.service.add_item(watchlist.id, "token", "sol")
        self.service.add_item(first.id, "narrative", "Solana ecosystem")

        matches = self.service.find_matching_watchlists(signal_event(token="sol"))
        self.assertEqual([item.watchlist.name for item in matches], ["Also SOL", "Focused"])
        self.assertEqual(
            self.service.find_matching_watchlists(signal_event(hype=79)),
            [matches[0]],
        )
        self.assertEqual(
            len(self.service.find_matching_watchlists(
                signal_event(narrative="Solana ecosystem expansion")
            )),
            2,
        )

    def test_narrative_matching_normalizes_case_and_whitespace_but_stays_exact(self) -> None:
        watchlist = self.service.create_watchlist("Narratives")
        self.service.add_item(watchlist.id, "narrative", "AI Agents")

        exact = signal_event(token="TAO", narrative=" ai   agents ")
        expanded = signal_event(token="TAO", narrative="AI Agents infrastructure")

        self.assertEqual(
            [match.watchlist.name for match in self.service.find_matching_watchlists(exact)],
            ["Narratives"],
        )
        self.assertEqual(self.service.find_matching_watchlists(expanded), [])

    def test_event_association_publication_and_single_telegram_alert(self) -> None:
        for name in ("Portfolio", "High Priority"):
            watchlist = self.service.create_watchlist(name)
            self.service.add_item(watchlist.id, "token", "SOL")
        bus = EventBus()
        telegram = FakeTelegram()
        received = []
        register_default_subscribers(bus, self.db, telegram)
        bus.subscribe(WatchlistMatched, received.append)

        bus.publish(signal_event())

        self.assertEqual(len(received), 1)
        self.assertEqual(set(received[0].watchlist_names), {"Portfolio", "High Priority"})
        self.assertEqual(len(self.db.get_signal_watchlists(received[0].signal_id)), 2)
        self.assertEqual(len(telegram.alerts), 1)
        self.assertEqual(set(telegram.alerts[0][1]), {"Portfolio", "High Priority"})

    def test_rule_conditions_accept_watchlist_context(self) -> None:
        facts = SignalFacts(
            token="SOL",
            narrative="Solana ecosystem",
            hype_score=85,
            momentum_score=75,
            confidence=8,
            mentions=4,
            outcome_success_rate=0,
            watchlists=("Main Portfolio",),
            watchlist_ids=(7,),
            watchlist_priority=9,
            matched_watchlist=True,
        )
        condition = {
            "AND": [
                {"field": "watchlist", "operator": "eq", "value": "main portfolio"},
                {"field": "watchlist_id", "operator": "eq", "value": 7},
                {"field": "watchlist_priority", "operator": ">=", "value": 8},
                {"field": "matched_watchlist", "operator": "eq", "value": True},
            ]
        }
        self.assertTrue(evaluate_condition(condition, facts))

    def test_event_rule_engine_reads_persisted_watchlist_context(self) -> None:
        watchlist = self.service.create_watchlist("Main Portfolio", priority=9)
        self.service.add_item(watchlist.id, "token", "SOL")
        RuleService(self.db).create_rule(
            "Focused research",
            {
                "AND": [
                    {"field": "watchlist", "operator": "eq", "value": "Main Portfolio"},
                    {"field": "hype_score", "operator": ">=", "value": 80},
                ]
            },
            ["high_priority", "dashboard_highlight"],
        )
        bus = EventBus()
        register_default_subscribers(bus, self.db, None)

        bus.publish(signal_event())

        matches = self.db.get_rule_matches()
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["rule_name"], "Focused research")
        self.assertEqual(int(matches[0]["high_priority"]), 1)

    def test_report_uses_associated_outcomes(self) -> None:
        watchlist = self.service.create_watchlist("AI Narratives")
        self.service.add_item(watchlist.id, "narrative", "AI Agents")
        signal_id = self.db.save_signal_history(
            "narrative", None, "AI Agents", 84, 79, 9, "research", 4
        )
        self.db.save_signal_watchlist(signal_id, watchlist.id, "narrative", "AI Agents")
        self.db.save_signal_outcome(signal_id, 24, "SUCCESS", 8, 2, 7, "Continued")

        report = self.service.report(watchlist.id)

        self.assertEqual(report.signals_count, 1)
        self.assertEqual(report.evaluated_count, 1)
        self.assertEqual(report.success_rate, 100.0)
        self.assertEqual(report.average_hype_score, 84.0)

    def test_empty_report_collects_outcomes(self) -> None:
        watchlist = self.service.create_watchlist("Empty")
        report = self.service.report(watchlist.id)
        self.assertEqual(report.signals_count, 0)
        self.assertIsNone(report.success_rate)

    def test_dashboard_api_crud_filters_and_validation(self) -> None:
        client = TestClient(create_app(self.db.path))
        try:
            created = client.post(
                "/api/watchlists",
                json={"name": "Dashboard Portfolio", "priority": 8},
            )
            self.assertEqual(created.status_code, 201)
            watchlist_id = created.json()["id"]
            item = client.post(
                f"/api/watchlists/{watchlist_id}/items",
                json={"item_type": "token", "item_value": "SOL"},
            )
            self.assertEqual(item.status_code, 201)
            self.assertEqual(
                client.post(
                    f"/api/watchlists/{watchlist_id}/items",
                    json={"item_type": "token", "item_value": "sol"},
                ).status_code,
                409,
            )
            self.assertEqual(
                client.post(
                    "/api/watchlists",
                    json={"name": "Bad", "minimum_confidence": 11},
                ).status_code,
                422,
            )
            signal_id = self.db.save_signal_history(
                "token", "SOL", None, 80, 70, 8, "research", 4
            )
            self.db.save_signal_watchlist(signal_id, watchlist_id, "token", "SOL")
            self.assertEqual(
                len(client.get(
                    f"/api/signals?watchlist_id={watchlist_id}"
                ).json()["signals"]),
                1,
            )
            self.assertEqual(client.get("/watchlists").status_code, 200)
            self.assertIn("Dashboard Portfolio", client.get(
                f"/watchlists/{watchlist_id}"
            ).text)
            self.assertEqual(
                client.get(f"/api/watchlists/{watchlist_id}/history").status_code,
                200,
            )
            self.assertEqual(
                client.delete(f"/api/watchlists/{watchlist_id}").status_code,
                204,
            )
        finally:
            client.close()

    def test_csv_exports_watchlists_items_and_associations(self) -> None:
        watchlist = self.service.create_watchlist("Portfolio")
        self.service.add_item(watchlist.id, "token", "SOL")
        signal_id = self.db.save_signal_history(
            "token", "SOL", None, 80, 70, 8, "research", 4
        )
        self.db.save_signal_watchlist(signal_id, watchlist.id, "token", "SOL")
        exporter = CSVExportService(self.db, self.root / "exports")

        result = exporter.export(
            ["watchlists", "watchlist_signals"],
            watchlist_name="Portfolio",
        )

        self.assertEqual({item.kind for item in result.files}, {
            "watchlists", "watchlist_items", "watchlist_signals"
        })
        for exported in result.files:
            self.assertEqual(exported.path.read_bytes()[:3], b"\xef\xbb\xbf")
        signal_file = next(
            item.path for item in result.files if item.kind == "watchlist_signals"
        )
        with signal_file.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows[0]["token"], "SOL")

    def test_telegram_watchlist_names_are_html_escaped(self) -> None:
        message = format_telegram_hype_alert(
            signal_event().alert,
            ("Portfolio <script>", "AI & DePIN"),
        )
        self.assertIn("Portfolio &lt;script&gt;", message)
        self.assertIn("AI &amp; DePIN", message)
        self.assertNotIn("<script>", message)

    def test_cli_watchlist_arguments_are_backward_compatible(self) -> None:
        with patch.object(
            sys,
            "argv",
            ["app.main", "--add-watchlist-token", "Main Portfolio", "BTC"],
        ):
            args = parse_args()
        self.assertEqual(args.add_watchlist_token, ["Main Portfolio", "BTC"])
        self.assertEqual(args.mode, "live")


class WatchlistMigrationTests(unittest.TestCase):
    def test_existing_database_initializes_watchlist_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "legacy.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE legacy_data (id INTEGER PRIMARY KEY)")
            connection.commit()
            connection.close()

            db = Database(path)
            db.initialize()
            try:
                self.assertTrue(db.has_table("legacy_data"))
                self.assertTrue(db.has_table("watchlists"))
                self.assertTrue(db.has_table("watchlist_items"))
                self.assertTrue(db.has_table("signal_watchlists"))
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
