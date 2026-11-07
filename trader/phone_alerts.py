"""Optional threshold-crossing wallet push alerts via ntfy.sh.

The *desktop* app observes live wallet states. No extra requests are sent to
the in-game auction house. Topics are bearer secrets on public ntfy.sh, so
generate an unpredictable topic and do not share it.
"""
from __future__ import annotations

import re
import secrets
import urllib.request

_TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{16,96}$")
NTFY_ORIGIN = "https://ntfy.sh"


def generate_topic() -> str:
    """A 160-bit random topic; knowing the topic allows others to subscribe."""
    return "ft-" + secrets.token_hex(20)


def validate_topic(topic: str) -> str:
    topic = str(topic).strip()
    if not _TOPIC_RE.fullmatch(topic):
        raise ValueError("Use a private ntfy topic of 16-96 letters, numbers, - or _. Generate one for best security.")
    return topic


def threshold_copper(silver: str | int) -> int:
    """1s=100c; alert threshold is a positive, whole number of silver."""
    raw = str(silver).strip()
    if not re.fullmatch(r"[0-9]+", raw):
        raise ValueError("Threshold must be a whole number of silver.")
    number = int(raw)
    if not 1 <= number <= 1_000_000:
        raise ValueError("Threshold must be from 1 to 1,000,000 silver.")
    return number * 100


class WalletCrossing:
    """Emit once on a below-to-at-or-above transition; rearm after falling below.

    Initial observations establish a baseline. They do not notify if the user
    opens the desktop app with an already-high wallet.
    """

    def __init__(self):
        self.previous: int | None = None

    def reset(self) -> None:
        self.previous = None

    def observe(self, money_copper: int, threshold: int) -> bool:
        if type(money_copper) is not int or money_copper < 0:
            return False
        if type(threshold) is not int or threshold <= 0:
            return False
        previous = self.previous
        self.previous = money_copper
        return previous is not None and previous < threshold <= money_copper


def publish_ntfy(topic: str, message: str, *, title: str = "Forever Trader",
                 timeout: float = 8.0) -> None:
    """Send a single notification. Caller runs this on a background thread."""
    safe_topic = validate_topic(topic)
    request = urllib.request.Request(
        f"{NTFY_ORIGIN}/{safe_topic}",
        data=message.encode("utf-8"),
        headers={"Title": title, "Priority": "high", "Tags": "moneybag",
                 "Content-Type": "text/plain; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(f"ntfy returned HTTP {response.status}")
