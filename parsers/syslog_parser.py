"""Log parsing: TextFSM for structured device output + regex for syslog lines.

This turns raw router/syslog text into normalized events that feed feature
engineering. TextFSM templates live in parsers/templates/.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Iterable

# Common precursor signatures seen in NOC syslogs.
SYSLOG_PATTERNS = {
    "bgp_flap": re.compile(r"%BGP-5-ADJCHANGE:.*neighbor (?P<peer>\S+) (?P<state>Up|Down)"),
    "ospf_change": re.compile(r"%OSPF-5-ADJCHG:.*from (?P<from>\S+) to (?P<to>\S+)"),
    "ldp_event": re.compile(r"%LDP-5-(?P<event>\w+):"),
    "intf_updown": re.compile(r"Interface (?P<intf>\S+), changed state to (?P<state>up|down)"),
    "ipsec_rekey": re.compile(r"IPSEC.*(?P<event>rekey|SA expired).*peer (?P<peer>\S+)"),
}


@dataclass
class Event:
    ts: str
    host: str
    kind: str
    fields: dict


def parse_syslog_line(ts: str, host: str, line: str) -> Event | None:
    for kind, pat in SYSLOG_PATTERNS.items():
        m = pat.search(line)
        if m:
            return Event(ts=ts, host=host, kind=kind, fields=m.groupdict())
    return None


def parse_stream(lines: Iterable[tuple[str, str, str]]) -> list[dict]:
    """lines: iterable of (timestamp, host, raw_line)."""
    out: list[dict] = []
    for ts, host, line in lines:
        ev = parse_syslog_line(ts, host, line)
        if ev:
            out.append(asdict(ev))
    return out


# TextFSM example (for `show interface` style output):
#   Save as parsers/templates/show_interface.textfsm and load with textfsm.TextFSM.
# Value INTF (\S+)
# Value IN_RATE (\d+)
# Value OUT_RATE (\d+)
# Start
#   ^${INTF} is up -> Continue
#   ^\s+input rate ${IN_RATE} -> Continue
#   ^\s+output rate ${OUT_RATE} -> Record

if __name__ == "__main__":
    sample = [
        ("2026-06-18T12:00:00", "PE1", "%BGP-5-ADJCHANGE: neighbor 10.0.0.2 Down"),
        ("2026-06-18T12:00:01", "PE2", "Interface eth2, changed state to down"),
    ]
    for e in parse_stream(sample):
        print(e)
