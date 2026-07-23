import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.ai.analyzer import SpikeInsight
from app.alerts.telegram import HypeAlert
from app.alerts.telegram import TelegramAlerter
from app.dashboard.app import create_app
from app.db.database import Database
from app.events import EventBus, SignalCreated
from app.events.subscribers import register_default_subscribers
from app.export.csv_exporter import CSVExportService
from app.rules import (
    RuleService,
    RuleValidationError,
    SignalFacts,
    evaluate_condition,
    normalize_actions,
    validate_condition,
)
from app.scoring.hype_score import HypeSignal


class FakeTelegram:
    def __init__(self) -> None:
        self.hype_alerts = []
        self.rule_alerts = []

    def send_hype_alert(self, alert) -> None:
        self.hype_alerts.append(alert)

    def send_rule_alert(self, name, priority, facts) -> None:
        self.rule_alerts.append((name, priority, facts))


def signal_event(
    *,
    token: str = "SOL",
    narrative: str = "Solana ecosystem",
    hype: float = 88,
    momentum: float = 76,
    confidence: int = 8,
    mentions: int = 4,
) -> SignalCreated:
    alert = HypeAlert(
        signal=HypeSignal(
            kind="token",
            name=token,
            mentions_count=mentions,
            average_importance=9,
            hype_score=hype,
        ),
        insight=SpikeInsight(
            explanation="Rule engine test signal",
            action="research",
            confidence=confidence,
        ),
        top_posts=[],
        related_tokens=[token],
        related_narratives=[narrative],
        momentum=[],
        baseline_mentions_count=mentions,
    )
    event = SignalCreated.from_alert(alert)
    return SignalCreated(
        alert=event.alert,
        signal_type="token + narrative",
        token=token,
        narrative=narrative,
        hype_score=hype,
        momentum_score=momentum,
        confidence=confidence,
        action=event.action,
        mentions_count=mentions,
    )


class RuleConditionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.facts = SignalFacts(
            token="SOL",
            narrative="Solana ecosystem",
            hype_score=80,
            momentum_score=72,
            confidence=8,
            mentions=4,
            outcome_success_rate=75,
        )

    def test_and_or_not_expression(self) -> None:
        condition = {
            "AND": [
                {"field": "token", "operator": "eq", "value": "sol"},
                {
                    "OR": [
                        {"field": "hype_score", "operator": ">=", "value": 80},
                        {"field": "mentions", "operator": ">", "value": 10},
                    ]
                },
                {
                    "NOT": {
                        "field": "narrative",
                        "operator": "contains",
                        "value": "Gaming",
                    }
                },
            ]
        }

        self.assertTrue(evaluate_condition(condition, self.facts))

    def test_numeric_threshold_boundaries_are_inclusive(self) -> None:
        for field, value in (
            ("hype_score", 80),
            ("momentum_score", 72),
            ("confidence", 8),
            ("mentions", 4),
            ("outcome_success_rate", 75),
        ):
            self.assertTrue(
                evaluate_condition(
                    {"field": field, "operator": ">=", "value": value},
                    self.facts,
                )
            )

    def test_invalid_field_operator_and_empty_actions_are_rejected(self) -> None:
        with self.assertRaises(RuleValidationError):
            validate_condition({"field": "price", "operator": ">", "value": 1})
        with self.assertRaises(RuleValidationError):
            validate_condition(
                {"field": "token", "operator": ">", "value": "SOL"}
            )
        with self.assertRaises(RuleValidationError):
            normalize_actions([])

    @patch("app.alerts.telegram.requests.post")
    def test_telegram_rule_alert_escapes_dynamic_content(self, post) -> None:
        post.return_value.raise_for_status.return_value = None
        facts = SignalFacts("SOL<script>", None, 90, 80, 9, 5, 75)

        TelegramAlerter("token", "chat").send_rule_alert(
            "Whale <watch>",
            100,
            facts,
        )

        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["parse_mode"], "HTML")
        self.assertIn("Whale &lt;watch&gt;", payload["text"])
        self.assertIn("SOL&lt;script&gt;", payload["text"])
        self.assertNotIn("<script>", payload["text"])


class RulePersistenceAndEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "rules.sqlite3"
        self.db = Database(self.database_path)
        self.db.initialize()
        self.service = RuleService(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self.temp_dir.cleanup()

    def test_schema_contains_requested_rule_fields(self) -> None:
        columns = {
            row["name"]
            for row in self.db.connection.execute("PRAGMA table_info(alert_rules)")
        }
        self.assertEqual(
            columns,
            {
                "id",
                "name",
                "enabled",
                "priority",
                "condition",
                "action",
                "created_at",
                "updated_at",
                "last_triggered",
                "trigger_count",
            },
        )

    def test_crud_orders_by_priority_and_preserves_rules_on_reset(self) -> None:
        low = self.service.create_rule(
            "Low",
            {"field": "hype_score", "operator": ">=", "value": 40},
            ["dashboard_highlight"],
            priority=1,
        )
        high = self.service.create_rule(
            "High",
            {"field": "hype_score", "operator": ">=", "value": 80},
            ["high_priority"],
            priority=100,
        )
        self.assertEqual([rule.id for rule in self.service.list_rules()], [high.id, low.id])

        updated = self.service.update_rule(low.id, enabled=False, priority=5)
        self.assertFalse(updated.enabled)
        self.assertEqual(updated.priority, 5)
        self.db.reset()
        self.assertEqual(len(self.service.list_rules()), 2)
        self.assertTrue(self.service.delete_rule(high.id))
        self.assertIsNone(self.service.get_rule(high.id))

    def test_signal_created_triggers_actions_once_and_sets_markers(self) -> None:
        telegram = FakeTelegram()
        rule = self.service.create_rule(
            "Strong SOL",
            {
                "AND": [
                    {"field": "token", "operator": "eq", "value": "SOL"},
                    {"field": "hype_score", "operator": ">=", "value": 80},
                ]
            },
            [
                "telegram",
                "high_priority",
                "dashboard_highlight",
                "include_in_digest",
                "csv_export_marker",
            ],
            priority=90,
        )
        bus = EventBus()
        register_default_subscribers(bus, self.db, telegram)
        event = signal_event()

        bus.publish(event)
        bus.publish(event)

        saved_rule = self.service.get_rule(rule.id)
        self.assertEqual(saved_rule.trigger_count, 2)
        self.assertEqual(len(telegram.rule_alerts), 2)
        signals = self.db.get_signals(limit=None)
        self.assertEqual(len(signals), 2)
        self.assertTrue(all(row["high_priority"] for row in signals))
        self.assertTrue(all(row["dashboard_highlight"] for row in signals))
        self.assertTrue(all(row["include_in_digest"] for row in signals))
        self.assertTrue(all(row["csv_export_marker"] for row in signals))

        export = CSVExportService(
            self.db,
            Path(self.temp_dir.name) / "exports",
        ).export(("signals",))
        with export.files[0].path.open(encoding="utf-8-sig", newline="") as handle:
            exported = list(csv.DictReader(handle))
        self.assertEqual(exported[0]["csv_export_marker"], "1")

        self.assertFalse(
            self.db.save_rule_match(signals[0]["id"], rule.id, rule.actions)
        )
        self.assertEqual(self.service.get_rule(rule.id).trigger_count, 2)

    def test_disabled_rule_does_not_trigger(self) -> None:
        rule = self.service.create_rule(
            "Disabled",
            {"field": "token", "operator": "eq", "value": "SOL"},
            ["dashboard_highlight"],
            enabled=False,
        )
        bus = EventBus()
        register_default_subscribers(bus, self.db, None)
        bus.publish(signal_event())

        self.assertEqual(self.service.get_rule(rule.id).trigger_count, 0)
        self.assertFalse(self.db.get_signals(1)[0]["dashboard_highlight"])

    def test_outcome_success_rate_condition_uses_saved_outcomes(self) -> None:
        original_id = self.db.save_signal_history(
            "narrative",
            None,
            "Solana ecosystem",
            60,
            50,
            7,
            "watch",
            2,
        )
        self.db.save_signal_outcome(
            original_id,
            24,
            "SUCCESS",
            10,
            1,
            5,
            "continued",
        )
        rule = self.service.create_rule(
            "Proven narrative",
            {
                "field": "outcome_success_rate",
                "operator": ">=",
                "value": 100,
            },
            ["dashboard_highlight"],
        )
        bus = EventBus()
        register_default_subscribers(bus, self.db, None)
        bus.publish(signal_event())

        self.assertEqual(self.service.get_rule(rule.id).trigger_count, 1)

    def test_test_rule_is_a_dry_run(self) -> None:
        rule = self.service.create_rule(
            "Dry run",
            {"field": "confidence", "operator": ">=", "value": 8},
            ["telegram"],
        )
        result = self.service.test_rule(rule.id, {"confidence": 8})

        self.assertTrue(result.matched)
        self.assertEqual(self.service.get_rule(rule.id).trigger_count, 0)


class RuleApiAndCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "api-rules.sqlite3"
        self.client = TestClient(create_app(self.database_path))

    def tearDown(self) -> None:
        self.client.close()
        self.temp_dir.cleanup()

    def test_rule_api_crud_and_pages(self) -> None:
        payload = {
            "name": "Extreme AI",
            "enabled": True,
            "priority": 50,
            "condition": {
                "AND": [
                    {"field": "narrative", "operator": "contains", "value": "AI"},
                    {"field": "momentum_score", "operator": ">=", "value": 70},
                ]
            },
            "action": ["dashboard_highlight", "include_in_digest"],
        }
        created = self.client.post("/api/rules", json=payload)
        self.assertEqual(created.status_code, 201)
        rule_id = created.json()["id"]

        listing = self.client.get("/api/rules")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["rules"][0]["name"], "Extreme AI")
        self.assertIn("Smart Alert Rules", self.client.get("/rules").text)
        self.assertIn("Rule definition", self.client.get(f"/rules/{rule_id}").text)

        updated = self.client.put(
            f"/api/rules/{rule_id}",
            json={"enabled": False, "priority": 75},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertFalse(updated.json()["enabled"])
        self.assertEqual(updated.json()["priority"], 75)

        deleted = self.client.delete(f"/api/rules/{rule_id}")
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(self.client.get("/api/rules").json()["rules"], [])
        self.assertEqual(self.client.get(f"/rules/{rule_id}").status_code, 404)

    def test_rule_api_rejects_invalid_conditions(self) -> None:
        response = self.client.post(
            "/api/rules",
            json={
                "name": "Invalid",
                "condition": {"field": "price", "operator": ">", "value": 1},
                "action": ["telegram"],
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_cli_create_list_test_disable_enable_and_delete(self) -> None:
        env = os.environ.copy()
        env["DATABASE_PATH"] = str(self.database_path)
        condition = json.dumps(
            {"field": "hype_score", "operator": ">=", "value": 80}
        )
        created = self._run_cli(
            env,
            "--create-rule",
            "CLI rule",
            "--rule-condition",
            condition,
            "--rule-actions",
            "dashboard_highlight,csv_export_marker",
            "--rule-priority",
            "20",
        )
        self.assertIn("Rule created", created.stderr)
        self.assertIn("CLI rule", self._run_cli(env, "--list-rules").stderr)
        signal_json = json.dumps({"hype_score": 80})
        self.assertIn(
            "MATCH",
            self._run_cli(
                env,
                "--test-rule",
                "1",
                "--signal-json",
                signal_json,
            ).stderr,
        )
        self.assertIn("disabled", self._run_cli(env, "--disable-rule", "1").stderr)
        self.assertIn("enabled", self._run_cli(env, "--enable-rule", "1").stderr)
        self.assertIn("deleted", self._run_cli(env, "--delete-rule", "1").stderr)

    @staticmethod
    def _run_cli(env: dict[str, str], *arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "app.main", *arguments],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )


if __name__ == "__main__":
    unittest.main()
