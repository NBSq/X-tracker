import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from app.main import (
    EnrichedCandidate,
    build_signal_history_record,
    merge_alert_candidates,
    send_candidate_alert,
)
from app.events import EventBus, SignalCreated
from app.scoring.momentum_score import NarrativeMomentum
from app.scoring.hype_score import HypeCandidate, HypeSignal, should_merge_candidates


def make_candidate(kind: str, name: str, post_ids: set[str]) -> EnrichedCandidate:
    signal = HypeSignal(
        kind=kind,
        name=name,
        mentions_count=len(post_ids),
        average_importance=8.0,
        hype_score=len(post_ids) * 8.0,
    )
    return EnrichedCandidate(
        candidate=HypeCandidate(signal=signal, post_ids=frozenset(post_ids)),
        rows=[],
    )


class AlertMergingTests(unittest.TestCase):
    def test_custom_event_bus_receives_signal_without_direct_side_effects(self) -> None:
        rows = [
            {
                "post_id": "1",
                "username": "analyst",
                "text": "BTC momentum is rising",
                "tokens_json": '["BTC"]',
                "narratives_json": '["Bitcoin / macro"]',
                "importance": 9,
            }
        ]
        candidate = make_candidate("token", "BTC", {"1"})
        candidate = EnrichedCandidate(candidate=candidate.candidate, rows=rows)
        analyzer = Mock()
        analyzer.explain_spike.return_value = SimpleNamespace(
            explanation="Momentum increased",
            action="research",
            confidence=8,
        )
        telegram = Mock()
        database = Mock()
        events = []
        event_bus = EventBus()
        event_bus.subscribe(SignalCreated, events.append)

        send_candidate_alert(
            candidate,
            None,
            [],
            analyzer,
            telegram,
            database,
            event_bus,
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].token, "BTC")
        database.save_signal_history.assert_not_called()
        telegram.send_hype_alert.assert_not_called()

    def test_merges_token_and_narrative_with_mostly_same_posts(self) -> None:
        token = make_candidate("token", "BTC", {"1", "2", "3"})
        narrative = make_candidate("narrative", "Bitcoin / macro", {"1", "2", "4"})

        groups = merge_alert_candidates([token, narrative])

        self.assertTrue(should_merge_candidates(token.candidate, narrative.candidate))
        self.assertEqual(len(groups), 1)
        self.assertIsNotNone(groups[0][1])

    def test_keeps_alerts_separate_when_posts_are_substantially_different(self) -> None:
        token = make_candidate("token", "BTC", {"1", "2", "3"})
        narrative = make_candidate("narrative", "Regulation", {"3", "4", "5"})

        groups = merge_alert_candidates([token, narrative])

        self.assertFalse(should_merge_candidates(token.candidate, narrative.candidate))
        self.assertEqual(len(groups), 2)
        self.assertTrue(all(merged is None for _, merged in groups))

    def test_does_not_merge_two_tokens(self) -> None:
        btc = make_candidate("token", "BTC", {"1", "2", "3"})
        eth = make_candidate("token", "ETH", {"1", "2", "3"})

        self.assertEqual(len(merge_alert_candidates([btc, eth])), 2)

    def test_merged_alert_hype_uses_unique_post_importance(self) -> None:
        rows = [
            {
                "post_id": str(index),
                "username": "analyst",
                "text": f"Post {index}",
                "tokens_json": '["BTC"]',
                "narratives_json": '["Bitcoin / macro"]',
                "importance": importance,
            }
            for index, importance in enumerate((10, 9, 8), start=1)
        ]
        token = make_candidate("token", "BTC", {"1", "2", "3"})
        narrative = make_candidate("narrative", "Bitcoin / macro", {"1", "2", "3"})
        token = EnrichedCandidate(candidate=token.candidate, rows=rows)
        narrative = EnrichedCandidate(candidate=narrative.candidate, rows=rows)
        analyzer = Mock()
        analyzer.explain_spike.return_value = SimpleNamespace(
            explanation="Shared posts",
            action="research",
            confidence=8,
        )
        telegram = Mock()
        database = Mock()

        send_candidate_alert(
            token,
            narrative,
            [NarrativeMomentum(name="Bitcoin / macro", score=90)],
            analyzer,
            telegram,
            database,
        )

        alert = telegram.send_hype_alert.call_args.args[0]
        self.assertEqual(alert.merged_hype_score, 27.0)
        self.assertNotEqual(alert.merged_hype_score, 48.0)

    def test_identical_98_point_components_remain_98_when_merged(self) -> None:
        importances = [9] * 10 + [8]
        rows = [
            {
                "post_id": str(index),
                "username": "analyst",
                "text": f"Post {index}",
                "tokens_json": '["BTC"]',
                "narratives_json": '["Bitcoin / macro"]',
                "importance": importance,
            }
            for index, importance in enumerate(importances, start=1)
        ]
        post_ids = frozenset(str(index) for index in range(1, 12))
        token = EnrichedCandidate(
            candidate=HypeCandidate(
                signal=HypeSignal("token", "BTC", 11, 98 / 11, 98.0),
                post_ids=post_ids,
            ),
            rows=rows,
        )
        narrative = EnrichedCandidate(
            candidate=HypeCandidate(
                signal=HypeSignal(
                    "narrative",
                    "Bitcoin / macro",
                    11,
                    98 / 11,
                    98.0,
                ),
                post_ids=post_ids,
            ),
            rows=rows,
        )
        analyzer = Mock()
        analyzer.explain_spike.return_value = SimpleNamespace(
            explanation="Shared posts",
            action="research",
            confidence=8,
        )
        telegram = Mock()

        send_candidate_alert(
            token,
            narrative,
            [],
            analyzer,
            telegram,
            Mock(),
        )

        alert = telegram.send_hype_alert.call_args.args[0]
        self.assertEqual(alert.merged_hype_score, 98.0)

    def test_signal_history_record_for_merged_alert(self) -> None:
        rows = [
            {
                "post_id": "1",
                "username": "analyst",
                "text": "Post 1",
                "tokens_json": '["BTC"]',
                "narratives_json": '["Bitcoin / macro"]',
                "importance": 9,
            }
        ]
        token = make_candidate("token", "BTC", {"1"})
        narrative = make_candidate("narrative", "Bitcoin / macro", {"1"})
        token = EnrichedCandidate(candidate=token.candidate, rows=rows)
        narrative = EnrichedCandidate(candidate=narrative.candidate, rows=rows)
        analyzer = Mock()
        analyzer.explain_spike.return_value = SimpleNamespace(
            explanation="Shared posts",
            action="research",
            confidence=8,
        )
        telegram = Mock()
        database = Mock()

        send_candidate_alert(token, narrative, [], analyzer, telegram, database)

        alert = telegram.send_hype_alert.call_args.args[0]
        record = build_signal_history_record(alert)
        self.assertEqual(record["signal_type"], "token + narrative")
        self.assertEqual(record["token"], "BTC")
        self.assertEqual(record["narrative"], "Bitcoin / macro")
        self.assertEqual(record["confidence"], 8)
        database.save_signal_history.assert_called_once()


if __name__ == "__main__":
    unittest.main()
