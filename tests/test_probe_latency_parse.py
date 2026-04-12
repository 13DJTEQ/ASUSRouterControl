from __future__ import annotations

from asusroutercontrol._time import utcnow
from asusroutercontrol.probes import _parse_ping


def test_parse_ping_integer_loss_summary() -> None:
    output = """PING 76.94.96.1 (76.94.96.1): 56 data bytes
--- 76.94.96.1 ping statistics ---
20 packets transmitted, 20 packets received, 0% packet loss
round-trip min/avg/max = 6.325/8.426/11.955 ms
"""
    probe = _parse_ping(output, "gateway", utcnow())
    assert probe.samples == 20
    assert probe.loss_pct == 0.0
    assert probe.avg_ms == 8.426


def test_parse_ping_decimal_loss_summary() -> None:
    output = """PING 1.1.1.1 (1.1.1.1): 56 data bytes
--- 1.1.1.1 ping statistics ---
20 packets transmitted, 19 packets received, 5.0% packet loss
round-trip min/avg/max = 10.100/14.500/20.900 ms
"""
    probe = _parse_ping(output, "cloudflare", utcnow())
    assert probe.samples == 20
    assert probe.loss_pct == 5.0
    assert probe.avg_ms == 14.5
