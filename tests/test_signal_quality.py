import csv
import json
import tempfile
import unittest
from argparse import Namespace
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import load_config
from app.dashboard.app import create_app
from app.db.database import Database
from app.events import (
    EventBus, QualityDegradationDetected, QualityImprovementDetected,
    SignalEvaluated, SignalQualityCalculated,
)
from app.events.subscribers import SignalQualitySubscriber
from app.export.csv_exporter import (
    QUALITY_AGGREGATE_COLUMNS,
    QUALITY_RECOMMENDATION_COLUMNS,
    SIGNAL_QUALITY_COLUMNS,
    CSVExportService,
)
from app.main import (
    parse_args, requested_csv_exports, requested_quality_command,
    run_quality_command,
)
from app.quality import QualityBreakdown, QualityScoreCalculator, SignalQualityService
from app.rules import RuleEngine, RuleService, SignalFacts, evaluate_condition


class QualityCalculatorTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config()
        self.calculator = QualityScoreCalculator(self.config)

    def test_unavailable_dimensions_are_renormalized(self):
        score, classification = self.calculator.calculate(
            QualityBreakdown(outcome_quality=100, evidence_strength=50),
            evidence_count=10,
        )
        expected = (100 * 0.25 + 50 * 0.10) / 0.35
        self.assertAlmostEqual(score, expected, places=2)
        self.assertEqual(classification, "insufficient_data")

    def test_classification_thresholds_and_bounds(self):
        self.assertEqual(self.calculator.classify(85), "excellent")
        self.assertEqual(self.calculator.classify(70), "strong")
        self.assertEqual(self.calculator.classify(55), "moderate")
        self.assertEqual(self.calculator.classify(40), "weak")
        self.assertEqual(self.calculator.classify(39.99), "unreliable")
        score, _ = self.calculator.calculate(
            QualityBreakdown(
                outcome_quality=500, confidence_calibration=500,
                evidence_strength=500,
            ),
            10,
        )
        self.assertEqual(score, 100)

    def test_evidence_conflicts_reduce_strength(self):
        values = dict(
            source_count=3, item_count=3, author_count=3, highest_priority=8,
            supporting_factor_count=2, matched_entity_count=2, update_count=1,
        )
        clean = self.calculator.evidence_strength(**values, conflict_count=0)
        conflicted = self.calculator.evidence_strength(**values, conflict_count=2)
        self.assertGreater(clean, conflicted)

    def test_confidence_mapping_and_calibration(self):
        self.assertEqual(self.calculator.confidence_probability(8), 0.8)
        success = self.calculator.calibration(8, "SUCCESS")
        failure = self.calculator.calibration(8, "FAILED")
        under = self.calculator.calibration(2, "SUCCESS")
        self.assertAlmostEqual(success["calibration_error"], 0.2)
        self.assertTrue(failure["overconfident"])
        self.assertTrue(under["underconfident"])
        self.assertLess(failure["score"], success["score"])

    def test_timeliness_and_missing_timestamp(self):
        self.assertEqual(self.calculator.timeliness(None), None)
        self.assertEqual(self.calculator.timeliness(5), 100)
        self.assertGreater(self.calculator.timeliness(15), self.calculator.timeliness(60))

    def test_noise_states_are_deterministic(self):
        base = dict(
            maximum_movement=0, evidence_strength=20, source_count=1,
            conflict_count=0, watchlist_count=0, ai_confidence=3, eligible=True,
        )
        self.assertEqual(
            self.calculator.noise_risk(outcome_status="FAILED", **base)[1],
            "confirmed_noise",
        )
        self.assertEqual(
            self.calculator.noise_risk(outcome_status=None, **base)[1],
            "probable_noise",
        )
        base.update(evidence_strength=90, source_count=3, watchlist_count=1, ai_confidence=8)
        self.assertEqual(
            self.calculator.noise_risk(outcome_status=None, **base)[1],
            "unevaluated",
        )

    def test_ai_agreement_uses_distinct_providers(self):
        def analysis(provider, action="research", confidence=8):
            return {
                "provider": provider, "action": action, "risk_level": "medium",
                "confidence": confidence, "related_tokens_json": '["SOL"]',
                "related_narratives_json": '["Solana ecosystem"]',
                "supporting_factors_json": '["volume"]',
            }
        score = self.calculator.ai_agreement([
            analysis("mock"), analysis("mock", "ignore"), analysis("openai")
        ])
        self.assertEqual(score, 100)

    def test_source_reliability_uses_smoothing_and_minimum_sample(self):
        score, sufficient = self.calculator.source_reliability(
            evaluated=1, successful=1, neutral=0, failed=0,
            successful_fetches=1, failed_fetches=0, duplicate_ratio=0.35,
            conflict_rate=0, average_ingestion_minutes=3,
        )
        self.assertFalse(sufficient)
        self.assertLess(score, 100)


class SignalQualityIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = Database(self.root / "quality.sqlite3")
        self.db.initialize()
        self.config = replace(load_config(), database_path=self.db.path)
        self.events = []
        self.bus = EventBus()
        self.bus.subscribe(SignalQualityCalculated, self.events.append)
        self.service = SignalQualityService(self.db, self.config, self.bus)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def signal(self, *, outcome="SUCCESS", token="SOL", narrative="Solana ecosystem"):
        signal_id = self.db.save_signal_history(
            "token + narrative", token, narrative, 70, 75, 8, "research", 4,
        )
        self.db.connection.execute(
            "UPDATE signal_history SET timestamp = datetime('now', '-25 hours') WHERE id = ?",
            (signal_id,),
        )
        self.db.connection.commit()
        self.db.save_signal_outcome(
            signal_id, 24, outcome, 15 if outcome == "SUCCESS" else -15,
            3 if outcome == "SUCCESS" else -3,
            18 if outcome == "SUCCESS" else -18, "quality test",
        )
        rule_id = self.db.create_alert_rule(
            f"rule-{signal_id}", True, 5,
            {"field": "token", "operator": "eq", "value": token},
            ("dashboard_highlight",),
        )
        self.db.save_rule_match(signal_id, rule_id, ("dashboard_highlight",))
        return signal_id

    def test_calculates_persists_breakdown_and_publishes_event(self):
        signal_id = self.signal()
        result = self.service.calculate_signal(signal_id)
        stored = self.db.get_signal_quality_score(signal_id, 1)
        self.assertGreaterEqual(result.quality_score, 0)
        self.assertLessEqual(result.quality_score, 100)
        self.assertIsNotNone(stored)
        self.assertIn("noise_state", json.loads(stored["breakdown_json"])["details"])
        self.assertEqual(self.events[-1].signal_id, signal_id)

    def test_recalculation_is_idempotent_and_versions_are_separate(self):
        signal_id = self.signal()
        self.service.calculate_signal(signal_id)
        self.service.calculate_signal(signal_id)
        self.assertEqual(len(self.db.get_signal_quality_scores(limit=None)), 1)
        self.service.calculate_signal(signal_id, version=2)
        self.assertEqual(len(self.db.get_signal_quality_scores(limit=None)), 2)

    def test_aggregate_coverage_precision_and_noise(self):
        self.service.calculate_signal(self.signal(outcome="SUCCESS"))
        self.service.calculate_signal(self.signal(outcome="FAILED", token="BTC", narrative="Bitcoin / macro"))
        aggregate = self.service.aggregate("overall", "all")
        self.assertEqual(aggregate.evaluated_count, 2)
        self.assertEqual(aggregate.precision, 50)
        self.assertEqual(aggregate.evaluation_coverage, 100)
        self.assertGreater(aggregate.noise_rate, 0)

    def test_recommendations_deduplicate_and_status_updates(self):
        self.service.calculate_signal(self.signal(outcome="FAILED"))
        first = self.service.generate_recommendations()
        second = self.service.generate_recommendations()
        self.assertEqual(len(first), len(second))
        self.assertGreater(len(first), 0)
        recommendation_id = int(first[0]["id"])
        self.assertTrue(self.service.update_recommendation(recommendation_id, "acknowledged"))
        row = next(row for row in self.service.recommendations() if row["id"] == recommendation_id)
        self.assertEqual(row["status"], "acknowledged")

    def test_quality_rule_fields_evaluate_and_engine_matches(self):
        signal_id = self.signal()
        score = self.service.calculate_signal(signal_id)
        condition = {
            "AND": [
                {"field": "signal_quality_score", "operator": ">=", "value": score.quality_score},
                {"field": "noise_risk", "operator": "<", "value": 100},
            ]
        }
        self.assertTrue(evaluate_condition(condition, SignalFacts(
            token="SOL", narrative="Solana ecosystem", hype_score=70,
            momentum_score=75, confidence=8, mentions=4,
            outcome_success_rate=100, signal_quality_score=score.quality_score,
            noise_risk=score.breakdown.noise_risk,
        )))
        rule = RuleService(self.db).create_rule(
            "quality gate", condition, ["include_in_digest"], priority=7,
        )
        RuleEngine(self.db, rule_scope="quality").evaluate_saved_signal(signal_id)
        self.assertTrue(any(row["rule_id"] == rule.id for row in self.db.get_rule_matches(signal_id)))

    def test_validation_and_backward_compatible_migration(self):
        tables = {
            row["name"] for row in self.db.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertTrue({
            "signal_quality_scores", "quality_aggregates", "quality_recommendations"
        }.issubset(tables))
        signal_id = self.signal()
        self.service.calculate_signal(signal_id)
        self.assertEqual(self.service.validate(), [])

    def test_csv_exports_headers_bom_and_cli_selection(self):
        self.service.calculate_signal(self.signal())
        self.service.generate_recommendations()
        output = self.root / "exports"
        result = CSVExportService(self.db, output).export((
            "signal_quality", "source_quality", "rule_quality",
            "watchlist_quality", "ai_quality", "quality_recommendations",
        ))
        self.assertEqual(len(result.files), 6)
        signal_file = next(item.path for item in result.files if item.kind == "signal_quality")
        self.assertTrue(signal_file.read_bytes().startswith(b"\xef\xbb\xbf"))
        with signal_file.open(encoding="utf-8-sig", newline="") as handle:
            self.assertEqual(tuple(next(csv.reader(handle))), SIGNAL_QUALITY_COLUMNS)
        recommendation_file = next(
            item.path for item in result.files if item.kind == "quality_recommendations"
        )
        with recommendation_file.open(encoding="utf-8-sig", newline="") as handle:
            self.assertEqual(tuple(next(csv.reader(handle))), QUALITY_RECOMMENDATION_COLUMNS)
        with patch("sys.argv", ["app.main", "--export-rule-quality-csv"]):
            self.assertIn("rule_quality", requested_csv_exports(parse_args()))

    def test_dashboard_pages_and_api_filters(self):
        signal_id = self.signal()
        self.service.calculate_signal(signal_id)
        client = TestClient(create_app(self.db.path, config=self.config))
        for path in (
            "/quality", f"/quality/signals/{signal_id}", "/quality/sources",
            "/quality/rules", "/quality/watchlists", "/quality/ai",
            "/quality/narratives", "/quality/tokens", "/quality/recommendations",
        ):
            self.assertEqual(client.get(path).status_code, 200, path)
        response = client.get("/api/quality/signals?limit=1&classification=strong")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["limit"], 1)
        self.assertEqual(client.get("/api/quality/signals?limit=0").status_code, 422)
        self.assertEqual(client.get(f"/api/quality/signals/{signal_id}").status_code, 200)
        self.assertEqual(client.get("/api/quality/validate").status_code, 200)

    def test_empty_database_quality_dashboard_renders(self):
        client = TestClient(create_app(self.db.path, config=self.config))
        response = client.get("/quality")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Signal Quality Overview", response.text)

    def test_graph_metadata_keeps_original_weight(self):
        signal_id = self.signal()
        now = "2026-07-28T00:00:00+00:00"
        node_id = self.db.upsert_graph_node(
            node_type="token", entity_id="SOL", label="SOL", normalized_label="sol",
            weight=0.7, activity_score=0.6, first_seen_at=now, last_seen_at=now,
            metadata_json="{}",
        )
        self.service.calculate_signal(signal_id)
        node = self.db.connection.execute(
            "SELECT * FROM graph_nodes WHERE id = ?", (node_id,)
        ).fetchone()
        self.assertEqual(node["weight"], 0.7)
        metadata = json.loads(node["metadata_json"])
        self.assertIn("reliability_adjusted_weight", metadata)

    def test_period_comparison_publishes_improvement_and_degradation(self):
        improvements, degradations = [], []
        self.bus.subscribe(QualityImprovementDetected, improvements.append)
        self.bus.subscribe(QualityDegradationDetected, degradations.append)
        current_id = self.signal(outcome="SUCCESS")
        previous_id = self.signal(
            outcome="FAILED", token="BTC", narrative="Bitcoin / macro"
        )
        self.db.connection.execute(
            "UPDATE signal_history SET timestamp = datetime('now', '-35 days') WHERE id = ?",
            (previous_id,),
        )
        self.db.connection.commit()
        self.service.calculate_signal(current_id)
        self.service.calculate_signal(previous_id)
        comparison = self.service.compare_periods("overall", "all", 30)
        self.assertEqual(comparison["quality_score"]["classification"], "improved")
        self.assertEqual(len(improvements), 1)

        self.db.connection.execute(
            "UPDATE signal_quality_scores SET quality_score = CASE signal_id WHEN ? THEN 5 ELSE 95 END",
            (current_id,),
        )
        self.db.connection.commit()
        comparison = self.service.compare_periods("overall", "all", 30)
        self.assertEqual(comparison["quality_score"]["classification"], "degraded")
        self.assertEqual(len(degradations), 1)

    def test_incremental_outcome_event_recalculates_signal(self):
        signal_id = self.signal()
        first = self.service.calculate_signal(signal_id)
        subscriber = SignalQualitySubscriber(self.service, self.db)
        subscriber.signal_evaluated(SignalEvaluated(
            signal_id, 24, "FAILED", -20, -3, -20, "changed",
        ))
        current = self.db.get_signal_quality_score(signal_id)
        self.assertEqual(current["id"], first.id)
        self.assertGreaterEqual(len(self.events), 2)

    def test_ai_provider_and_model_reports_include_fallback_metrics(self):
        signal_id = self.signal()

        def analysis(provider, model, fallback):
            return {
                "provider": provider, "model": model, "prompt_version": "v1",
                "summary": "summary", "why_it_matters": "evidence",
                "action": "research", "confidence": 8, "risk_level": "medium",
                "supporting_factors": ["source diversity"], "risk_factors": [],
                "related_tokens": ["SOL"],
                "related_narratives": ["Solana ecosystem"],
                "market_context": "neutral", "invalidation_conditions": [],
                "cached": False, "fallback_used": fallback,
            }

        self.db.save_signal_ai_analysis(signal_id, analysis("mock", "rules-v1", True))
        self.db.save_signal_ai_analysis(signal_id, analysis("openai", "gpt-test", False))
        self.service.calculate_signal(signal_id)
        report = self.service.ai_report()
        providers = {row["entity_id"]: row for row in report if row["entity_type"] == "ai_provider"}
        self.assertEqual(providers["mock"]["fallback_rate"], 100)
        self.assertFalse(providers["openai"]["ranking_eligible"])
        self.assertIn("gpt-test", {row["entity_id"] for row in report})

    def test_entity_reports_cover_rules_narratives_and_tokens(self):
        self.service.calculate_signal(self.signal())
        for entity_type in ("rule", "narrative", "token"):
            report = self.service.entity_report(entity_type)
            self.assertEqual(len(report), 1)
            self.assertIn("metrics", report[0])

    def test_empty_quality_csvs_still_have_headers(self):
        result = CSVExportService(self.db, self.root / "empty").export(("signal_quality",))
        with result.files[0].path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(tuple(rows[0]), SIGNAL_QUALITY_COLUMNS)
        self.assertEqual(len(rows), 1)

    def test_api_date_validation_and_recommendation_status(self):
        self.service.calculate_signal(self.signal(outcome="FAILED"))
        recommendation_id = self.service.generate_recommendations()[0]["id"]
        client = TestClient(create_app(self.db.path, config=self.config))
        self.assertEqual(
            client.get("/api/quality/signals?from_date=2026-99-99").status_code,
            422,
        )
        response = client.put(
            f"/api/quality/recommendations/{recommendation_id}",
            json={"status": "resolved"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "resolved")

    def test_cli_quality_summary_and_recalculation_dispatch(self):
        self.service.calculate_signal(self.signal())
        values = {
            "quality_summary": True, "quality_signal": None,
            "quality_sources": False, "quality_rules": False,
            "quality_watchlists": False, "quality_narratives": False,
            "quality_tokens": False, "quality_ai": False,
            "quality_recommendations": False, "quality_recalculate": False,
            "quality_validate": False, "quality_period_days": 30,
            "quality_version": None, "quality_entity": None,
        }
        args = Namespace(**values)
        self.assertTrue(requested_quality_command(args))
        with patch("app.main.logger.info") as logged:
            run_quality_command(args, self.config, self.db)
        self.assertTrue(logged.called)

        args = Namespace(**{**values, "quality_summary": False, "quality_recalculate": True})
        with patch("app.main.logger.info") as logged:
            run_quality_command(args, self.config, self.db)
        self.assertIn("Quality recalculation complete", logged.call_args.args[0])

    def test_cli_parser_accepts_all_quality_reports(self):
        flags = (
            "--quality-summary", "--quality-sources", "--quality-rules",
            "--quality-watchlists", "--quality-narratives", "--quality-tokens",
            "--quality-ai", "--quality-recommendations", "--quality-recalculate",
            "--quality-validate",
        )
        for flag in flags:
            with self.subTest(flag=flag), patch("sys.argv", ["app.main", flag]):
                self.assertTrue(requested_quality_command(parse_args()))
        with patch("sys.argv", ["app.main", "--quality-signal", "12"]):
            self.assertEqual(parse_args().quality_signal, 12)


if __name__ == "__main__":
    unittest.main()
