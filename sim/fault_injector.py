# -*- coding: utf-8 -*-
"""Fault injection for the SD-WAN/MPLS sim.

This is one of your core original contributions: it injects realistic precursor
conditions AND records ground-truth labels alongside telemetry so the prediction
engine has clean training data.

Usage:
    python -m sim.fault_injector --scenario mpls_degradation --target PE1
    python -m sim.fault_injector --scenario clear              # remove all impairments

When the real containerlab sim is unavailable, this module exposes
`apply_scenario()` which the synthetic data generator imports to shape its
time-series, keeping labels consistent across synthetic and real runs.

DRY_RUN:
    Set DRY_RUN=1 to print every shell command without executing it.
    This mirrors the convention used in sim/netem_faults.py and is the safe way
    to verify what will happen before running on a live lab:

        DRY_RUN=1 python -m sim.fault_injector --scenario congestion --target PE1
"""
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from typing import Sequence

# ---------------------------------------------------------------------------
# DRY_RUN  – mirrors sim/netem_faults.py exactly so both scripts share the
# same environment variable and print format.
# ---------------------------------------------------------------------------
DRY_RUN: bool = os.getenv("DRY_RUN", "0") == "1"

# Container name prefix written by `containerlab deploy -t topology.clab.yml`.
# containerlab creates containers named  <prefix>-<node>  e.g. clab-airgap-noc-pe1.
CLAB_PREFIX: str = os.getenv("CLAB_PREFIX", "clab-airgap-noc")

# How many ramp steps to use when interpolating tc/vtysh parameters over time.
# More steps = smoother ramp, but each step blocks for ramp_seconds/N_STEPS seconds.
N_STEPS: int = int(os.getenv("FAULT_STEPS", "10"))


# ---------------------------------------------------------------------------
# Fault class label space (must match ml.train)
# ---------------------------------------------------------------------------
LABELS = {
    0: "nominal",
    1: "congestion",
    2: "bgp_instability",
    3: "mpls_degradation",
    4: "policy_drift",
}
LABEL_IDS = {v: k for k, v in LABELS.items()}


@dataclass
class Scenario:
    name: str
    target: str
    # ramp duration (seconds) over which precursors build before "impact"
    ramp_seconds: int
    # which telemetry features degrade and by how much, in ABSOLUTE units added
    # per minute. Additive (not multiplicative) so counter-style signals that
    # start at zero — BGP flaps, QoS violations, ACL mismatches — actually grow.
    drift: dict


SCENARIOS = {
    "congestion": Scenario(
        name="congestion", target="PE1", ramp_seconds=120,
        drift={"if_utilization": 7.0, "latency_ms": 4.0, "queue_drops": 2.0},
    ),
    "bgp_instability": Scenario(
        name="bgp_instability", target="PE1", ramp_seconds=90,
        drift={"bgp_flaps": 1.4, "route_churn": 2.5, "path_asymmetry": 0.4},
    ),
    "mpls_degradation": Scenario(
        name="mpls_degradation", target="PE2", ramp_seconds=100,
        drift={"tunnel_loss": 0.9, "jitter_ms": 2.5, "rekey_anomaly": 0.7},
    ),
    "policy_drift": Scenario(
        name="policy_drift", target="CE2", ramp_seconds=150,
        drift={"qos_violations": 2.2, "acl_mismatch": 2.6},
    ),
}


# ---------------------------------------------------------------------------
# Helpers (mirrors netem_faults._node / _run so both files share the pattern)
# ---------------------------------------------------------------------------

def _container(node: str) -> str:
    """Return the full Docker container name for a topology node.

    containerlab names containers  <CLAB_PREFIX>-<node_lowercase>, e.g.
    CLAB_PREFIX=clab-airgap-noc  node=PE1  →  clab-airgap-noc-pe1
    """
    return f"{CLAB_PREFIX}-{node.lower()}"


def _run(cmd: str) -> None:
    """Print (DRY_RUN) or execute a shell command.

    Uses subprocess.run with check=False so a failing tc/vtysh command does not
    abort the whole ramp loop — the NOC should keep injecting even if one step
    hits a transient error (e.g. qdisc already exists).
    """
    if DRY_RUN:
        print(f"[dry-run] {cmd}")
        return
    subprocess.run(shlex.split(cmd), check=False)


def _docker(node: str, inner: str) -> str:
    """Build a `docker exec <container> <inner>` command string."""
    return f"docker exec {_container(node)} {inner}"


def _vtysh(node: str, vtysh_cmd: str) -> str:
    """Build a `docker exec … vtysh -c '…'` command string.

    Single quotes around the vtysh command are required because vtysh arguments
    contain spaces (e.g. 'clear bgp 10.0.0.12').
    """
    return f"docker exec {_container(node)} vtysh -c '{vtysh_cmd}'"


# ---------------------------------------------------------------------------
# Per-scenario ramp implementations
# ---------------------------------------------------------------------------

def _ramp_congestion(node: str, iface: str, progress: float, step: int) -> None:
    """Simulate link congestion via a TBF (token-bucket filter) + netem chain.

    Drift mapping (from SCENARIOS['congestion'].drift):
      if_utilization  →  TBF rate limit (squeezes the pipe, raises utilisation)
      latency_ms      →  netem base delay
      queue_drops     →  netem packet loss (rises as the TBF bucket fills)

    Parameters at full ramp (progress=1.0):
      rate  = 2 mbit   (PE1 uplink squeezed hard; default is ~1 Gbps veth)
      delay = 60 ms
      loss  = 5 %

    At progress=0 we issue a `qdisc del` to ensure a clean starting state,
    then replace/add on every subsequent step so tc sees fresh parameters.
    """
    c = _container(node)

    # Interpolate tc parameters linearly across the ramp.
    # rate_kbit: 10000 kbit (full speed) → 500 kbit at progress=1
    # We clamp to at least 500 kbit so the container stays reachable.
    rate_kbit = max(500, int(10000 - 9500 * progress))

    # delay: 0 ms → 60 ms
    delay_ms = int(60 * progress)

    # loss: 0% → 5%  (kept modest so OSPF/BGP hold-timers don't expire)
    loss_pct = round(5.0 * progress, 1)

    if step == 0:
        # Remove any leftover qdisc from a previous run before we start.
        # `|| true` prevents subprocess.run from seeing an error exit code
        # if no qdisc exists (tc exits 2 in that case).
        _run(_docker(node, f"tc qdisc del dev {iface} root 2>/dev/null || true"))

    # Step 1: TBF root qdisc – rate-limits overall throughput.
    # `replace` is idempotent: works whether or not a qdisc already exists.
    # burst must be ≥ rate/HZ; 32 kbit is safe for rates ≥ 500 kbit.
    # latency 400ms = maximum queue size expressed as time (TBF drains at `rate`).
    _run(_docker(node,
        f"tc qdisc replace dev {iface} root handle 1: "
        f"tbf rate {rate_kbit}kbit burst 32kbit latency 400ms"))

    # Step 2: netem child qdisc – adds delay + loss ON TOP of TBF.
    # `add` the first time, then `change` so we don't stack qdiscs.
    # Distribution `normal` gives realistic jitter rather than flat random.
    if delay_ms > 0 or loss_pct > 0:
        action = "add" if step == 0 else "change"
        _run(_docker(node,
            f"tc qdisc {action} dev {iface} parent 1: handle 10: "
            f"netem delay {delay_ms}ms 5ms distribution normal loss {loss_pct}%"))

    print(f"[congestion t={int(progress * SCENARIOS['congestion'].ramp_seconds):3d}s] "
          f"rate={rate_kbit}kbit delay={delay_ms}ms loss={loss_pct}%")


def _ramp_bgp_instability(node: str, neighbor: str, progress: float, step: int) -> None:
    """Simulate BGP instability by hard-resetting the iBGP session to the far PE.

    There is no tc/netem knob that directly maps to bgp_flaps / route_churn.
    Instead we use FRR's `clear bgp` to tear down and re-establish the session,
    which triggers:
      - a BGP NOTIFICATION / TCP RST visible in show bgp summary
      - route withdrawals + re-advertisements (route_churn)
      - transient path asymmetry while the RIB reconverges

    Drift mapping (from SCENARIOS['bgp_instability'].drift):
      bgp_flaps       →  number of `clear bgp <neighbor>` resets issued
      route_churn     →  side-effect: all VPNv4 prefixes withdrawn + re-sent
      path_asymmetry  →  side-effect: some packets take alternate paths during
                         the convergence window (~30-60 s per reset)

    We issue a reset roughly every (ramp_seconds / bgp_flaps_total) seconds.
    The drift value 1.4 flaps/min over 90s ≈ 2 flaps total; we space them
    evenly across the ramp.

    `neighbor` should be the iBGP peer's loopback IP as configured in frr.conf,
    e.g. 10.0.0.12 (PE2's loopback) when injecting on PE1.
    """
    sc = SCENARIOS["bgp_instability"]

    # Total expected flaps over the full ramp = drift["bgp_flaps"] * ramp_minutes
    ramp_min = sc.ramp_seconds / 60.0
    total_flaps = sc.drift["bgp_flaps"] * ramp_min  # ≈ 2.1

    # Issue a reset only at the step indices that correspond to flap events.
    # Distribute flap events uniformly across N_STEPS.
    flap_steps = {
        round(i * (N_STEPS - 1) / max(total_flaps - 1, 1))
        for i in range(int(total_flaps))
    }

    if step in flap_steps:
        # `clear bgp <neighbor>` sends a BGP NOTIFICATION (cease) to the peer and
        # immediately re-initiates the TCP/BGP session.  This is the same effect as
        # a real link-flap-induced session teardown but is software-controlled.
        _run(_vtysh(node, f"clear bgp {neighbor}"))
        print(f"[bgp_instability t~={int(progress * sc.ramp_seconds):3d}s] "
              f"BGP RESET -> {neighbor} (flap event)")
    else:
        print(f"[bgp_instability t~={int(progress * sc.ramp_seconds):3d}s] "
              f"converging... (no reset this step)")


def _ramp_mpls_degradation(node: str, iface: str, progress: float, step: int) -> None:
    """Simulate MPLS tunnel degradation via packet loss + jitter on the core link.

    Drift mapping (from SCENARIOS['mpls_degradation'].drift):
      tunnel_loss   →  netem `loss`  (percent packet loss on the MPLS-labelled frames)
      jitter_ms     →  netem `delay … <jitter>ms` (variation around a base delay)
      rekey_anomaly →  represented by introducing a correlated loss pattern
                       (30% correlation) so loss bursts look like IKE rekey storms

    Parameters at full ramp (progress=1.0):
      loss     = 8 %   with 30 % correlation
      delay    = 20 ms ± 25 ms  (high jitter dominates)

    `replace` is used so the command is idempotent across steps.
    """
    # loss: 0% → 8%
    loss_pct = round(8.0 * progress, 1)

    # jitter: 0 ms → 25 ms (added as the variation arg to netem `delay`)
    jitter_ms = int(25 * progress)

    # base delay stays fixed; jitter grows
    base_delay_ms = 20

    if step == 0:
        _run(_docker(node, f"tc qdisc del dev {iface} root 2>/dev/null || true"))

    # netem with correlated loss: `loss <pct>% <corr>%` means each packet's loss
    # probability is correlated with the previous packet's outcome (Markov model).
    # 30% correlation produces burst losses that mimic IKE rekey timeouts better
    # than independent random loss.
    _run(_docker(node,
        f"tc qdisc replace dev {iface} root netem "
        f"loss {loss_pct}% 30% "           # % loss, 30% Markov correlation
        f"delay {base_delay_ms}ms {jitter_ms}ms distribution normal"))

    print(f"[mpls_degradation t={int(progress * SCENARIOS['mpls_degradation'].ramp_seconds):3d}s] "
          f"loss={loss_pct}% jitter=+/-{jitter_ms}ms")


def _ramp_policy_drift(node: str, iface: str, progress: float, step: int) -> None:
    """Simulate policy drift by progressively corrupting the QoS configuration.

    In production, a policy drift event happens when a change-control process
    applies an incorrect ACL or removes a QoS marking rule.  We approximate this
    by:
      1. Adding a low-priority netem qdisc that re-orders and sporadically drops
         packets (simulates a misapplied ACL dropping/misordering traffic).
      2. Progressively increasing reorder probability to mirror acl_mismatch drift.

    Drift mapping (from SCENARIOS['policy_drift'].drift):
      qos_violations  →  netem `reorder` probability (packets leave out-of-order)
      acl_mismatch    →  netem `loss` (mis-matched ACL entries silently drop pkts)

    Parameters at full ramp (progress=1.0):
      reorder  = 40%   with gap=3  (every 3rd+ packet may be reordered)
      loss     = 6%    (ACL false-positives)
    """
    reorder_pct = round(40.0 * progress, 1)
    loss_pct    = round(6.0  * progress, 1)

    if step == 0:
        _run(_docker(node, f"tc qdisc del dev {iface} root 2>/dev/null || true"))

    # netem reorder: `reorder <pct>% gap <n>` means every <n>th packet is sent
    # immediately while others are held for `delay` then released — this produces
    # the ACK-reordering signature that Telegraf's tcp_retransmit counter picks up.
    # A small base delay (1ms) is required for reorder to work in netem.
    _run(_docker(node,
        f"tc qdisc replace dev {iface} root netem "
        f"delay 1ms "                         # minimum delay required for reorder
        f"reorder {reorder_pct}% gap 3 "      # out-of-order delivery probability
        f"loss {loss_pct}%"))                 # ACL false-positive drops

    print(f"[policy_drift t={int(progress * SCENARIOS['policy_drift'].ramp_seconds):3d}s] "
          f"reorder={reorder_pct}% loss={loss_pct}%")


# ---------------------------------------------------------------------------
# Interface selection per scenario
# ---------------------------------------------------------------------------

# Which interface to impair on each scenario's default target node.
# The choice matches the link that carries the relevant traffic:
#   congestion      PE1 eth2  → the PE-P core uplink (MPLS-facing, high traffic)
#   mpls_degradation PE2 eth1 → the P-PE core downlink (labelled frames arrive here)
#   policy_drift    CE2 eth1  → the CE-PE customer uplink (where QoS/ACL is applied)
# bgp_instability uses vtysh (no interface needed, uses the iBGP peer IP instead).
_IFACE: dict[str, str] = {
    "congestion":       "eth2",   # PE1 → P1 (core-facing uplink)
    "mpls_degradation": "eth1",   # PE2 → P1 (core-facing downlink)
    "policy_drift":     "eth1",   # CE2 → PE2 (customer-facing)
}

# iBGP neighbor IPs for the bgp_instability scenario.
# PE1 resets its session to PE2's loopback; these must match frr.conf neighbor stmts.
_BGP_NEIGHBOR: dict[str, str] = {
    "PE1": "10.0.0.12",   # PE1 peers with PE2
    "PE2": "10.0.0.11",   # PE2 peers with PE1 (if --target PE2 is given)
}


# ---------------------------------------------------------------------------
# Public API: inject_live  (replaces the old stub)
# ---------------------------------------------------------------------------

def inject_live(scenario_name: str, target: str) -> None:  # pragma: no cover
    """Drive a fault on live containerlab nodes, ramping over sc.ramp_seconds.

    For each scenario the ramp is divided into N_STEPS equally-spaced ticks.
    At each tick we compute progress ∈ [0, 1] and call the scenario-specific
    ramp function which interpolates tc/vtysh parameters and issues the command.

    Set DRY_RUN=1 to print commands without executing them.
    Set FAULT_STEPS=<n> to change the ramp granularity (default 10).

    Args:
        scenario_name: One of the keys in SCENARIOS, or "clear".
        target:        Node name (e.g. "PE1"). Overrides SCENARIOS[name].target.
    """
    if scenario_name == "clear":
        _clear_live()
        return

    sc = SCENARIOS[scenario_name]
    step_duration = sc.ramp_seconds / N_STEPS  # seconds to sleep between steps

    print(
        f"[fault_injector] {'DRY-RUN ' if DRY_RUN else ''}injecting "
        f"'{sc.name}' on {target} | "
        f"ramp={sc.ramp_seconds}s steps={N_STEPS} step_duration={step_duration:.1f}s"
    )

    for step in range(N_STEPS + 1):
        # progress = 0.0 at step 0 (clean state), 1.0 at step N_STEPS (full fault).
        progress = step / N_STEPS

        if scenario_name == "congestion":
            iface = _IFACE["congestion"]
            _ramp_congestion(target, iface, progress, step)

        elif scenario_name == "bgp_instability":
            # Look up which iBGP peer to reset; fall back to the first value if
            # the target node isn't explicitly listed.
            neighbor = _BGP_NEIGHBOR.get(target, next(iter(_BGP_NEIGHBOR.values())))
            _ramp_bgp_instability(target, neighbor, progress, step)

        elif scenario_name == "mpls_degradation":
            iface = _IFACE["mpls_degradation"]
            _ramp_mpls_degradation(target, iface, progress, step)

        elif scenario_name == "policy_drift":
            iface = _IFACE["policy_drift"]
            _ramp_policy_drift(target, iface, progress, step)

        # Sleep between steps so impairment builds gradually.
        # Skip the sleep after the final step; skip real sleep in DRY_RUN.
        if step < N_STEPS:
            if DRY_RUN:
                # In dry-run mode use a tiny sleep so output is readable.
                time.sleep(0.05)
            else:
                time.sleep(step_duration)

    print(f"[fault_injector] '{sc.name}' ramp complete - impairments are ACTIVE on {target}")
    print(f"  Run with --scenario clear to remove all impairments.")


def _clear_live() -> None:  # pragma: no cover
    """Remove all tc qdiscs on every node and reset BGP sessions on the PEs.

    Called when --scenario clear is passed.  Idempotent: `|| true` absorbs the
    exit-2 that tc returns when no qdisc is present.
    """
    all_nodes  = ["CE1", "PE1", "P1", "PE2", "CE2"]
    core_nodes = ["PE1", "PE2"]          # nodes with LDP/BGP that need a BGP reset
    ifaces     = ["eth1", "eth2"]

    print(f"[fault_injector] {'DRY-RUN ' if DRY_RUN else ''}clearing all impairments")

    for node in all_nodes:
        for iface in ifaces:
            # Delete the root qdisc (and all children) from every interface.
            # If no qdisc was installed tc exits with code 2; `|| true` swallows it.
            _run(_docker(node, f"tc qdisc del dev {iface} root 2>/dev/null || true"))

    for node in core_nodes:
        # Soft-reset BGP on the PEs so they re-establish with clean state.
        # `clear ip bgp *` resets ALL sessions; use `clear bgp <peer>` to be
        # more surgical if you only want to restore one specific session.
        _run(_vtysh(node, "clear ip bgp *"))

    print("[fault_injector] clear complete - all nodes back to nominal state")


# ---------------------------------------------------------------------------
# apply_scenario: pure-Python telemetry shaper (no Docker, used by synthetic
# data generator and unit tests — keep this section unchanged).
# ---------------------------------------------------------------------------

def apply_scenario(base_row: dict, scenario_name: str, elapsed_s: float) -> dict:
    """Mutate a telemetry row to reflect a building fault. Returns the row with a
    `label` field set to the scenario's class id once precursors are present."""
    sc = SCENARIOS[scenario_name]
    row = dict(base_row)
    elapsed_min = elapsed_s / 60.0
    progress = min(elapsed_s / sc.ramp_seconds, 1.5)  # allow overshoot past impact
    for feat, per_min in sc.drift.items():
        # additive growth in absolute units per minute (works from a zero base)
        row[feat] = row.get(feat, 0.0) + per_min * elapsed_min
    # Label precursors as the fault class as soon as drift is meaningfully underway.
    row["label"] = LABEL_IDS[sc.name] if progress > 0.15 else 0
    row["time_to_impact_s"] = max(sc.ramp_seconds - elapsed_s, 0.0)
    return row


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Inject or clear faults on a live containerlab/FRR topology.",
        epilog=(
            "Examples:\n"
            "  DRY_RUN=1 python -m sim.fault_injector --scenario congestion\n"
            "  DRY_RUN=1 python -m sim.fault_injector --scenario bgp_instability --target PE1\n"
            "  python -m sim.fault_injector --scenario mpls_degradation --target PE2\n"
            "  python -m sim.fault_injector --scenario clear\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--scenario",
        choices=list(SCENARIOS) + ["clear"],
        required=True,
        help="Fault scenario to inject, or 'clear' to remove all impairments.",
    )
    ap.add_argument(
        "--target",
        default=None,
        help=(
            "Override the default target node for the scenario "
            "(e.g. --target PE2). Defaults to SCENARIOS[scenario].target."
        ),
    )
    args = ap.parse_args()

    effective_target = (
        args.target
        if args.target
        else (SCENARIOS[args.scenario].target if args.scenario != "clear" else "all")
    )
    inject_live(args.scenario, effective_target)
