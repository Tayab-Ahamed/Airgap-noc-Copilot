# Runbook: MPLS / SD-WAN Tunnel Degradation (fault class: mpls_degradation)

**ML fault class ID:** 3  
**Default target node:** PE2 (datacenter-side provider edge)  
**Monitored features:** tunnel_loss, jitter_ms, rekey_anomaly  
**Ramp time to impact:** ~100 seconds of building precursors before application-visible degradation

---

## 1. Symptom Recognition

MPLS tunnel degradation is insidious because the impairment builds gradually. The ML engine is designed to catch it 60–90 seconds before the jitter and loss reach thresholds that trigger traditional threshold alerts.

| Time relative to impact | Signal | Threshold warranting action |
|---|---|---|
| T-100 s | tunnel_loss metric rising (non-zero drop rate on MPLS-labelled frames) | >0.3% sustained loss for 60 s |
| T-80 s | jitter_ms increasing (variation in one-way delay on the PE-PE LSP) | >5 ms P95 variation |
| T-60 s | rekey_anomaly metric rising (IKE rekey events or SA renegotiation anomalies) | >0.5 normalized score |
| T-30 s | End-to-end latency from CE1 to CE2 increases | >25 ms above baseline |
| T-0 | Application degradation: real-time traffic drops, VoIP MOS drops, retransmits visible | VoIP MOS <3.5; TCP retransmit rate >2% |

**Key syslog signatures on PE2 (eth1 = core-facing link from P1):**

```
# From FRR's LDP daemon (ldpd):
%LDP-5-NBRCHG: neighbor 10.0.0.21 state changed to DOWN (Hold timer expired)
%LDP-4-LABELCHG: label binding changed for prefix 10.0.0.11/32

# From IPSec (strongSwan, if active on CE nodes):
charon: 07[IKE] IKE_SA ce1-to-ce2[1] state change: ESTABLISHED => DELETING
charon: 07[IKE] establishing IKE_SA ce1-to-ce2[1] between 10.0.0.1...10.0.0.2
```

**Distinguishing this from simple packet loss:** The tunnel_loss metric tracks loss specifically on MPLS-labelled frames between PEs, not general interface loss. If `show interface eth1` on PE2 shows no input errors but tunnel_loss is rising, the loss is happening within the P1-PE2 segment.

---

## 2. Root Causes (ranked by probability)

### 2a. Physical or optical degradation on the P1-PE2 link (P1:eth2 ↔ PE2:eth1) (~35%)
A degrading fibre connector, dirty SFP, or marginal optical power budget causes intermittent bit errors. TCP retransmissions double effective bandwidth consumption; MPLS frames are dropped in error.

**Distinguishing feature:** Interface error counters (CRC, input errors) rising on P1 eth2 or PE2 eth1. Loss is correlated with physical events, not software state.

```bash
docker exec clab-airgap-noc-p1  vtysh -c "show interface eth2" | grep -E "error|drop|CRC"
docker exec clab-airgap-noc-pe2 vtysh -c "show interface eth1" | grep -E "error|drop|CRC"
```

In the containerlab environment, errors are emulated via `tc netem` rather than real optical faults. In production, use OTDR or optical power readings.

### 2b. Congestion on P1 causing MPLS frame drops (~25%)
P1 is the only transit router in this topology. If both PE1-P1 and P1-PE2 links are near capacity, P1's internal forwarding path drops MPLS frames before they are queued. This is distinct from PE1 congestion (fault class 1) — here the bottleneck is inside the core.

**Distinguishing feature:** Both P1 eth1 (toward PE1) and P1 eth2 (toward PE2) show elevated utilization. PE1 eth2 may look fine because PE1 is not the bottleneck.

```bash
docker exec clab-airgap-noc-p1 vtysh -c "show interface eth1"
docker exec clab-airgap-noc-p1 vtysh -c "show interface eth2"
docker exec clab-airgap-noc-p1 tc -s qdisc show
```

### 2c. MTU mismatch causing MPLS fragmentation or black-holing (~20%)
MPLS labels add a 4-byte header per label to every packet. If the underlying Ethernet MTU was reduced (e.g. due to a path change, a new VPN label stack, or a misconfigured interface), packets larger than the effective MPLS MTU are silently dropped at P1 if the DF bit is set and the packet cannot be fragmented.

**Distinguishing feature:** Large pings succeed; small pings succeed; medium-to-large application traffic fails. Loss is 100% above a specific packet size.

```bash
# Test PMTUD: send pings of increasing size from CE1 toward CE2 via the MPLS path
docker exec clab-airgap-noc-ce1 ping -c 5 -s 1400 -M do 10.0.0.2   # likely to fail if MTU issue
docker exec clab-airgap-noc-ce1 ping -c 5 -s  800 -M do 10.0.0.2   # likely to succeed
docker exec clab-airgap-noc-ce1 ping -c 5 -s 1200 -M do 10.0.0.2   # bisect

# Check MPLS MTU on PE2's core interface
docker exec clab-airgap-noc-pe2 vtysh -c "show interface eth1" | grep -i mtu
docker exec clab-airgap-noc-p1  vtysh -c "show interface eth1" | grep -i mtu
docker exec clab-airgap-noc-p1  vtysh -c "show interface eth2" | grep -i mtu
```

Standard MPLS MTU for this topology: 1500 (Ethernet) - 4 (single MPLS label) = 1496 bytes. If double-stacking (VPN label + transport label), subtract 8 bytes.

### 2d. LDP session disruption causing label withdrawal (~10%)
If the LDP session between P1 and PE2 drops (e.g. due to a brief link interruption), P1 withdraws all label bindings for PE2's FEC (including the binding for PE2's loopback 10.0.0.12/32). During re-convergence, traffic to PE2 is black-holed.

**Distinguishing feature:** `show mpls ldp neighbor` on P1 shows PE2's LDP session in CONNECTING state. Loss is 100% during the LDP holddown period (~15 s by default).

```bash
docker exec clab-airgap-noc-p1  vtysh -c "show mpls ldp neighbor"
docker exec clab-airgap-noc-pe2 vtysh -c "show mpls ldp neighbor"
docker exec clab-airgap-noc-p1  vtysh -c "show mpls table"   # check if PE2 loopback has a label
```

### 2e. IPSec rekey storm causing synchronized drops (~10%)
When CE1 and CE2's IKE rekey timers are not staggered, both ends initiate a rekey simultaneously. During the renegotiation window (~1–3 seconds), encrypted packets are dropped. If the rekey period is short (e.g. 20 minutes), this happens frequently and is visible as periodic loss bursts.

**Distinguishing feature:** Loss is periodic and brief (1–3 s drops every ~20 min). The rekey_anomaly metric peaks in the telemetry data at regular intervals.

---

## 3. Remediation Actions (priority order)

### Step 1 — Isolate whether the loss is on the MPLS underlay or the IPSec overlay

```bash
# Test 1: Ping P1 from PE2 (MPLS underlay, no IPSec)
docker exec clab-airgap-noc-pe2 ping -c 20 -i 0.2 10.0.23.1   # P1 address on P1-PE1 link
docker exec clab-airgap-noc-pe2 ping -c 20 -i 0.2 10.0.0.21   # P1 loopback (via MPLS)

# Test 2: Ping CE1 loopback from CE2 (crosses the full MPLS path + IPSec if active)
docker exec clab-airgap-noc-ce2 ping -c 20 -i 0.2 10.0.0.1

# If Test 1 fails: loss is in the MPLS underlay → proceed to Step 2
# If Test 1 passes but Test 2 fails: loss is in the IPSec overlay → proceed to Step 4
```

### Step 2 — Check and restore the LDP/MPLS forwarding path

```bash
# Verify OSPF adjacencies are all up (prerequisite for LDP)
docker exec clab-airgap-noc-p1 vtysh -c "show ip ospf neighbor"
# Expect: PE1 (10.0.0.11) and PE2 (10.0.0.12) both in Full state

# Verify LDP sessions on P1
docker exec clab-airgap-noc-p1 vtysh -c "show mpls ldp neighbor"
# Expect: 10.0.0.11 (PE1) and 10.0.0.12 (PE2) both in ESTABLISHED state

# Verify label bindings: PE2 loopback should have a label on P1
docker exec clab-airgap-noc-p1 vtysh -c "show mpls table"
# Look for: 10.0.0.12/32 as a forwarding entry with a label stack

# If LDP session is down on P1-PE2:
docker exec clab-airgap-noc-p1 vtysh -c "clear mpls ldp neighbor 10.0.0.12"
# This forces an LDP session reset; re-establishment takes ~10-30 s
```

### Step 3 — Verify and fix the MTU configuration

```bash
# Check configured MTU on all core interfaces
docker exec clab-airgap-noc-pe1 vtysh -c "show interface eth2" | grep -i mtu
docker exec clab-airgap-noc-p1  vtysh -c "show interface eth1" | grep -i mtu
docker exec clab-airgap-noc-p1  vtysh -c "show interface eth2" | grep -i mtu
docker exec clab-airgap-noc-pe2 vtysh -c "show interface eth1" | grep -i mtu

# If any interface shows MTU < 1500, correct it:
docker exec clab-airgap-noc-p1 vtysh -c "
  configure terminal
  interface eth2
   mtu 1500
  end
  write
"

# Enable MPLS MTU propagation (adds ICMP too-big generation for MPLS paths)
docker exec clab-airgap-noc-p1 vtysh -c "
  configure terminal
  mpls ip
  end
"
```

### Step 4 — Stagger IPSec rekey timers on CE1 and CE2

To prevent synchronized rekey drops, offset the lifetimes:

On CE1 (`/etc/ipsec.conf` — update and reload):

```
conn ce1-to-ce2
    keylife    = 20m        # CE1 renegotiates after 20 minutes
    rekeymargin = 3m
```

On CE2 (`/etc/ipsec.conf` — offset by half the keylife):

```
conn ce2-to-ce1
    keylife    = 23m        # CE2 renegotiates after 23 minutes (staggered)
    rekeymargin = 3m
```

After editing, reload strongSwan:

```bash
docker exec clab-airgap-noc-ce1 ipsec reload
docker exec clab-airgap-noc-ce2 ipsec reload
```

### Step 5 — Apply netem correction if impairment was injected during a test

If the degradation was introduced by `sim/fault_injector.py --scenario mpls_degradation`:

```bash
# Remove the tc netem qdisc from PE2's core interface
docker exec clab-airgap-noc-pe2 tc qdisc del dev eth1 root 2>/dev/null || true

# Also clear the injection on P1 if it was applied there
docker exec clab-airgap-noc-p1 tc qdisc del dev eth2 root 2>/dev/null || true
```

---

## 4. Verification After Fix

```bash
# Loss should be zero or <0.1% for 5 consecutive minutes
docker exec clab-airgap-noc-ce1 ping -c 100 -i 0.1 10.0.0.2 | tail -3

# LDP sessions all established
docker exec clab-airgap-noc-p1 vtysh -c "show mpls ldp neighbor"

# MPLS forwarding table intact
docker exec clab-airgap-noc-p1 vtysh -c "show mpls table"

# Jitter: measure with hping3 if available, or observe the jitter_ms metric
# Target: jitter_ms < 3 ms on the PE-PE path in this lab environment

# ML risk score for PE2 should drop below 0.3
curl -s http://localhost:8000/api/nodes | python3 -m json.tool | grep -A5 PE2
```

---

## 5. Escalation Criteria

Escalate to Level 2 if any of the following:

- OSPF adjacency between P1 and PE2 has been down for more than 60 seconds and does not recover after manual interface reset.
- LDP session on P1-PE2 will not re-establish after 2 minutes — possible software bug in FRR's ldpd.
- Loss is confirmed at the physical layer (CRC errors rising) — requires physical inspection of the P1-PE2 fibre/SFP, out of scope for NOC.
- MTU mismatch is confirmed but the correct MTU value is unknown — requires network engineering to verify the path and label stack depth.
- The `rekey_anomaly` metric remains elevated after staggering IPSec timers — may indicate an IKE version incompatibility or certificate expiry.

---

## 6. What NOT To Do

- **Do NOT clear the entire MPLS forwarding table** (`clear mpls table`) as a first response. All LSPs are black-holed during the re-learning period (30–60 seconds).
- **Do NOT restart ospfd** on P1 to try to fix an LDP problem. OSPF re-convergence takes 30–90 seconds and all BGP next-hops become unreachable during that window.
- **Do NOT reduce the LDP hello interval below 5 seconds** to "speed up" recovery. A very short hello interval increases control-plane traffic and can cause cascading LDP drops under CPU load.
- **Do NOT change the MPLS label range** on any node without a maintenance window. Label range mismatches between LDP peers cause traffic to be forwarded to the wrong destination.
- **Do NOT diagnose IPSec rekey issues by disabling IPSec encryption** (setting `authby = none`). This removes authentication and encryption from customer VPN traffic; it is a security violation.
- **Do NOT assume the problem is fixed after a single successful ping.** MPLS degradation is characterized by intermittent loss; verify with a sustained 60-second ping or 1-minute hold on the metrics.
- **Do NOT reset the IPSec SA manually** (`ipsec down ce1-to-ce2 && ipsec up ce1-to-ce2`) unless the session is genuinely stuck. An unnecessary re-keying introduces a 1–3 second traffic gap.

---

## 7. Related Fault Classes

- **bgp_instability**: LDP session drops cause the iBGP loopback to become unreachable, which then causes BGP to drop. Always confirm the MPLS underlay is stable before investigating BGP.
- **congestion**: Congestion on P1 core links produces MPLS frame drops that appear as tunnel_loss. If congestion and mpls_degradation alerts co-occur, check P1 queue stats to determine whether congestion is the root cause.
- **policy_drift**: MTU changes introduced by a policy push at CE level can cause end-to-end fragmentation issues that appear as MPLS tunnel loss. Correlate with config change timestamps.
