"""Real fault-injection commands for the containerlab/FRR sim.

These run on YOUR Linux host (needs Docker + root). They use `docker exec` into
the FRR nodes plus `tc/netem` for impairment and vtysh for routing faults.

Each function returns the shell command(s) it runs so the backend can log the
exact action taken (good for the demo + audit trail). Set DRY_RUN=1 to print
without executing.

Mapping to fault classes (must stay in sync with sim/fault_injector.LABELS):
    congestion        -> egress rate limit + delay on a CE/PE uplink
    bgp_instability   -> flap a BGP neighbor via vtysh
    mpls_degradation  -> packet loss + jitter on the PE-PE path (tunnel)
    policy_drift      -> remove/alter a QoS/ACL stanza on a CE
"""
from __future__ import annotations

import os
import shlex
import subprocess
from typing import Sequence

DRY_RUN = os.getenv("DRY_RUN", "0") == "1"
CLAB_PREFIX = os.getenv("CLAB_PREFIX", "clab-airgap-noc")  # containerlab name prefix


def _node(name: str) -> str:
    return f"{CLAB_PREFIX}-{name.lower()}"


def _run(cmds: Sequence[str]) -> list[str]:
    executed = []
    for cmd in cmds:
        executed.append(cmd)
        if DRY_RUN:
            print(f"[dry-run] {cmd}")
            continue
        subprocess.run(shlex.split(cmd), check=False)
    return executed


def congestion(node: str = "PE1", iface: str = "eth1", rate: str = "5mbit",
               delay_ms: int = 40) -> list[str]:
    c = _node(node)
    return _run([
        f"docker exec {c} tc qdisc replace dev {iface} root handle 1: tbf rate {rate} burst 32kbit latency 400ms",
        f"docker exec {c} tc qdisc add dev {iface} parent 1: handle 10: netem delay {delay_ms}ms 10ms",
    ])


def mpls_degradation(node: str = "PE2", iface: str = "eth1", loss_pct: float = 4.0,
                     jitter_ms: int = 15) -> list[str]:
    c = _node(node)
    return _run([
        f"docker exec {c} tc qdisc replace dev {iface} root netem loss {loss_pct}% delay 20ms {jitter_ms}ms distribution normal",
    ])


def bgp_instability(node: str = "PE1", neighbor: str = "10.0.0.2", cycles: int = 3) -> list[str]:
    c = _node(node)
    cmds = []
    for _ in range(cycles):
        cmds.append(f"docker exec {c} vtysh -c 'clear bgp {neighbor}'")
    return _run(cmds)


def policy_drift(node: str = "CE2", iface: str = "eth1") -> list[str]:
    c = _node(node)
    # Remove an egress QoS/service-policy to simulate drift from golden config.
    return _run([
        f"docker exec {c} tc qdisc del dev {iface} root || true",
    ])


def clear_all(nodes: Sequence[str] = ("CE1", "PE1", "P1", "PE2", "CE2"),
              ifaces: Sequence[str] = ("eth1", "eth2")) -> list[str]:
    cmds = []
    for n in nodes:
        for i in ifaces:
            cmds.append(f"docker exec {_node(n)} tc qdisc del dev {i} root || true")
    return _run(cmds)


SCENARIO_FUNCS = {
    "congestion": congestion,
    "mpls_degradation": mpls_degradation,
    "bgp_instability": bgp_instability,
    "policy_drift": policy_drift,
}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario", choices=list(SCENARIO_FUNCS) + ["clear"])
    args = ap.parse_args()
    if args.scenario == "clear":
        print("\n".join(clear_all()))
    else:
        print("\n".join(SCENARIO_FUNCS[args.scenario]()))
