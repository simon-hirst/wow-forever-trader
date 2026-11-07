"""Phone alerts are deterministic and never contact the network in CI."""
import unittest
from unittest.mock import patch

from trader.phone_alerts import WalletCrossing, generate_topic, publish_ntfy, threshold_copper, validate_topic


class WalletAlertTests(unittest.TestCase):
    def test_silver_conversion_and_invalid_inputs(self):
        self.assertEqual(threshold_copper("30"), 3000)
        self.assertEqual(threshold_copper(12), 1200)
        for bad in ("0", "-1", "3.5", "abc", "1000001", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                threshold_copper(bad)

    def test_cross_once_then_rearm_below_threshold(self):
        alarm = WalletCrossing()
        self.assertFalse(alarm.observe(2900, 3000))
        self.assertFalse(alarm.observe(2999, 3000))
        self.assertTrue(alarm.observe(3000, 3000))
        self.assertFalse(alarm.observe(3400, 3000))
        self.assertFalse(alarm.observe(3000, 3000))
        self.assertFalse(alarm.observe(2999, 3000))
        self.assertTrue(alarm.observe(3050, 3000))

    def test_initial_balance_above_threshold_does_not_fire(self):
        alarm = WalletCrossing()
        self.assertFalse(alarm.observe(10000, 3000))
        self.assertFalse(alarm.observe(9500, 3000))
        alarm.reset()
        self.assertFalse(alarm.observe(2800, 3000))
        self.assertTrue(alarm.observe(3100, 3000))

    def test_invalid_frames_cannot_arm_or_fire(self):
        alarm = WalletCrossing()
        self.assertFalse(alarm.observe(-1, 3000))
        self.assertFalse(alarm.observe(None, 3000))
        self.assertFalse(alarm.observe(2000, 3000))
        self.assertFalse(alarm.observe(3000, 0))
        self.assertTrue(alarm.observe(3000, 3000))

    def test_random_topic_format_and_validity(self):
        topics = {generate_topic() for _ in range(8)}
        self.assertEqual(len(topics), 8)
        for topic in topics:
            self.assertEqual(validate_topic(topic), topic)
        for bad in ("small", "a/b/c", "contains space here", "https://ntfy.sh/example", "x"*97):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_topic(bad)

    def test_ntfy_publish_uses_post_and_high_priority(self):
        topic = generate_topic()
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
        with patch("trader.phone_alerts.urllib.request.urlopen", return_value=Response()) as send:
            publish_ntfy(topic, "Wallet reached 31s", title="Wallet threshold reached")
        req = send.call_args.args[0]
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.full_url, "https://ntfy.sh/" + topic)
        self.assertEqual(req.data, b"Wallet reached 31s")
        self.assertEqual(req.get_header("Priority"), "high")
        self.assertEqual(req.get_header("Title"), "Wallet threshold reached")
        self.assertEqual(send.call_args.kwargs["timeout"], 8.0)


if __name__ == "__main__":
    unittest.main()
