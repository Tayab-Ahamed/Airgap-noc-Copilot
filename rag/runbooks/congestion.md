# Runbook: Interface Congestion (fault class: congestion)

**ML fault class ID:** 1  
**Default target node:** PE1 (branch-side provider edge)  
**Monitored features:** if_utilization, latency_ms, queue_drops  
**Ramp time to impact:** ~120 seconds of building precursors before user-visible degradation

---

## 1. Symptom Recognition

An operator will typically see these signals, roughly in the order they appear:

| Time relative to impact | Signal | Threshold that warrants attention |
|---|---|---|
| T-120 s | if_utilization rising on PE1 eth2 | >65% sustained for 60 s |
| T-90 s | output queue drops appearing on PE1 eth2 | Any non-zero drop rate sustained >30 s |
| T-60 s | latency_ms drifting upward on traffic traversing PE1 | >20 ms above rolling 5-min baseline |
| T-30 s | Jitter variance increasing (jitter_ms metric) | >5 ms P95 variation |
| T-0 | User-reported slowness; voice/video QoS class begins dropping | RTP loss >1% |

**Syslog signatures to look for on PE1:**

```
%LINEPROTO-5-UPDOWN: Interface eth2, ...  (rare, but watch for flap under heavy load)
%QOS-3-POLICYDROP: Packet dropped by class-map VOICE ...
%OSPF-4-LSDB_OVERFLOW: ...  (if CPU-starved due to congestion)
```

**Note:** The copilot's ML engine flags this class when if_utilization and queue_drops are simultaneously trending above baseline even if neither has hit a hard threshold. Do not wait for alerts — act on the trend.

---

## 2. Root Causes (ranked by probability in this network)

### 2a. Burst traffic from a single application or site (most common, ~60%)
Branch CE1 hosts or a connected subnet is generating a large file transfer, backup job, or video conference that was not traffic-shaped at the CE. PE1's eth2 (the PE-P core uplink) is the first bottleneck.

**Distinguishing feature:** utilization spike is sharp and single-source; drops appear on one DSCP class only (BE/default).

### 2b. LSP load imbalance after a path reconvergence event (~20%)
A previous OSPF reconvergence or TE re-optimization shifted more traffic onto PE1-P1. The current LSP through P1 is now carrying traffic intended for a backup path.

**Distinguishing feature:** utilization began rising at the same time as a routing event in syslog. MPLS table shows all traffic on a single label.

### 2c. QoS policy missing or bypassed at CE1 (~10%)
The traffic shaper at CE1 (eth1 egress) was removed or mis-applied during a recent change window, allowing uncontrolled bursts to hit PE1.

**Distinguishing feature:** policy_drift alert may co-occur; check CE1 running config against golden.

### 2d. Hardware issue — SFP degradation causing spurious retransmissions (~5%)
A degrading optical link causes TCP streams to retransmit, doubling effective load.

**Distinguishing feature:** interface error counters (CRC, FCS) rising alongside utilization.

### 2e. DDoS reflection or scanning traffic entering the branch (~5%)
Unexpected traffic profile — many short flows, not correlated with business hours.

**Distinguishing feature:** flow data shows many small packets from diverse sources; no single top-talker but high PPS.

---

## 3. Remediation Actions (priority order)

> Work through these in sequence. Stop when congestion clears and metrics return to baseline for at least 5 minutes.

### Step 1 — Confirm the scope and source (do this first, takes <2 min)

```bash
# Check current utilization on PE1's uplink to the core
docker exec clab-airgap-noc-pe1 vtysh -c "show interface eth2"

# Confirm queue drops are on eth2 egress (not ingress)
docker exec clab-airgap-noc-pe1 tc -s qdisc show dev eth2

# Show top-N flows if sFlow/NetFlow is available
# (wire to your collector; not a containerlab built-in)
```

Look at the output queue drop counter. If it is zero, congestion is not at eth2 egress — re-examine eth1 (CE-facing) or look upstream at P1.

### Step 2 — Apply traffic shaping at the source CE (preferred; non-disruptive)

If the congestion source is a burst from CE1:

```bash
# Rate-limit CE1's uplink to PE1 to 80% of contracted rate (example: 8 mbit)
# This is a temporary measure; the golden config should have this already.
docker exec clab-airgap-noc-ce1 \
  tc qdisc replace dev eth1 root handle 1: tbf rate 8mbit burst 64kbit latency 200ms
docker exec clab-airgap-noc-ce1 \
  tc qdisc add dev eth1 parent 1: handle 10: sfq perturb 10
```

Verify that queue drops on PE1 eth2 stop within 30 seconds of applying this.

### Step 3 — Engage a backup LSP via traffic engineering

If PE1-P1 is saturated and an alternate P-PE path exists:

```bash
# On PE1: prefer an alternate MPLS path (requires TE pre-configuration)
docker exec clab-airgap-noc-pe1 vtysh -c "
  configure terminal
  interface eth2
   ip ospf cost 1000
  end
  write
"
# Raises OSPF cost on the congested link; traffic re-routes via alternate (if any).
# Lower the cost back to default (10) once congestion clears.
```

**In this 5-node linear topology (CE1-PE1-P1-PE2-CE2) there is no alternate PE-P path**, so TE re-routing is not available. Skip this step and proceed to Step 4 if you are on the default topology.

### Step 4 — Enforce QoS priority queuing on PE1

Protect real-time and high-priority traffic from being dropped during congestion:

```bash
# Verify QoS is active on PE1's egress toward P1
docker exec clab-airgap-noc-pe1 vtysh -c "show running-config" | grep policy-map

# If missing, apply a basic DSCP-based priority queueing:
docker exec clab-airgap-noc-pe1 vtysh -c "
  configure terminal
  class-map match-any REALTIME
   match dscp ef
  policy-map QOS-EGRESS
   class REALTIME
    priority percent 30
   class class-default
    fair-queue
  interface eth2
   service-policy output QOS-EGRESS
  end
  write
"
```

### Step 5 — Escalate if utilization remains >90% for >10 minutes

See Section 6 (Escalation Criteria).

---

## 4. Verification After Fix

Run these after applying any remediation step. Wait 3–5 minutes before declaring resolved.

```bash
# Interface utilization should be <70%
docker exec clab-airgap-noc-pe1 vtysh -c "show interface eth2" | grep "output rate"

# Queue drops should be zero or negligible
docker exec clab-airgap-noc-pe1 tc -s qdisc show dev eth2

# Latency to PE2 loopback should return to baseline (<15 ms in this lab)
docker exec clab-airgap-noc-pe1 ping -c 10 -i 0.5 10.0.0.12 | tail -3

# Confirm the ML risk score dropped below 0.3 in the backend
curl -s http://localhost:8000/api/nodes | python3 -m json.tool | grep -A3 PE1
```

---

## 5. Escalation Criteria

Escalate to network engineering (Level 2) if any of the following:

- Interface utilization remains >90% for more than 10 consecutive minutes after applying CE shaping.
- Congestion appears simultaneously on PE1 eth1 (CE-facing) AND eth2 (core-facing) — suggests capacity exhaustion, not a burst event.
- You identify the source as DDoS/scanning traffic — this is a security escalation as well as a network escalation.
- QoS remediation does not protect real-time class (voice dropping >5% even after policy applied).
- The alert recurs within 1 hour of clearing — suggests the root cause fix did not hold.

**Escalation contact:** Network Engineering on-call. Reference this alert ID and include the output of `show interface eth2` from PE1 and `tc -s qdisc show dev eth2`.

---

## 6. What NOT To Do

- **Do NOT reboot PE1** to clear the congestion. A reboot causes a full BGP/OSPF reconvergence (30–90 second outage for all VPN traffic) and does not fix the source of the load.
- **Do NOT simply raise the OSPF cost on eth2 without checking whether an alternate path exists.** In the default linear topology this just black-holes all traffic.
- **Do NOT apply rate limiting at PE1's eth1 (CE-facing ingress) before confirming at CE1.** Inbound shaping on PE1 discards packets that have already crossed the CE-PE link; you want to police at the source.
- **Do NOT clear the MPLS forwarding table or restart LDP** to try to relieve congestion. LDP re-convergence takes 20–60 seconds and will cause packet loss on all current LSPs simultaneously.
- **Do NOT mark this ticket resolved if utilization is <90% but queue drops are still non-zero.** Drops indicate QoS is actively shedding packets; baseline is zero drops in this environment.
- **Do NOT ignore a congestion alert during business hours even if utilization is "only" 75%.** The ML model flags this class 60–90 seconds before the threshold is hit; acting on the trend is the point.

---

## 7. Related Fault Classes

- **policy_drift**: Congestion that appears without a traffic burst may indicate QoS has been stripped. Check for co-occurring policy_drift alert.
- **mpls_degradation**: If congestion is on the P1 core link rather than the PE-CE access link, the presentation overlaps with MPLS degradation. Check which interface (eth1 vs eth2) is saturated.
- **bgp_instability**: Severe congestion can starve BGP keepalives on a shared-CPU FRR instance, causing a secondary BGP flap alert. Treat the congestion first.
