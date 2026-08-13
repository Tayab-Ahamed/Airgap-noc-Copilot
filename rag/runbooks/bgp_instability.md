# Runbook: BGP / Routing Instability (fault class: bgp_instability)

**ML fault class ID:** 2  
**Default target node:** PE1 (branch-side provider edge)  
**Monitored features:** bgp_flaps, route_churn, path_asymmetry  
**Ramp time to impact:** ~90 seconds of building precursors before VPN traffic impact

---

## 1. Symptom Recognition

BGP instability presents as a combination of control-plane and data-plane signals. The ML model detects the precursor phase — rising flap rate and route churn — well before VPN traffic is actually affected.

| Time relative to impact | Signal | Threshold warranting action |
|---|---|---|
| T-90 s | bgp_flaps metric rising on PE1 | >1 flap in 5-minute window |
| T-75 s | route_churn metric rising (withdrawal/re-advertisement cycles) | >3 prefix churn events per minute |
| T-60 s | path_asymmetry increasing (forward and reverse paths diverge) | >0.3 normalized score |
| T-30 s | SYSLOG: BGP ADJCHANGE Down/Up messages on PE1 | Any ADJCHANGE for an iBGP peer |
| T-0 | VPNv4 prefixes transiently absent from PE1 CUST VRF; ping drop to CE2 | Packet loss >5% |

**Key syslog messages to monitor on PE1:**

```
%BGP-5-ADJCHANGE: neighbor 10.0.0.12 Down Hold Timer Expired
%BGP-5-ADJCHANGE: neighbor 10.0.0.12 Up
%BGP-3-NOTIFICATION: sent to neighbor 10.0.0.12 6/2 (Hold Timer Expired)
%BGP-5-ADJCHANGE: neighbor 10.0.12.1 Down  (CE1 eBGP session — secondary effect)
```

**Key syslog messages on P1 to correlate:**

```
%OSPF-5-ADJCHG: Process 1, Nbr 10.0.0.11 on eth1 from FULL to DOWN, Neighbor Down
```

An OSPF adjacency drop on the underlay will always precede a BGP session drop if the root cause is a physical link failure. If BGP drops without an OSPF drop, the root cause is above the link layer (CPU starvation, timer misconfiguration, software bug).

---

## 2. Root Causes (ranked by probability)

### 2a. Hold-timer expiry due to CPU starvation on PE1 (~40%)
FRR's BGP process (bgpd) shares a Linux container with ospfd and ldpd. Under high CPU load — typically from a large routing table or a burst of route updates — bgpd may fail to send keepalives within the hold-time window (default 90 s, keepalive 30 s), causing the peer to declare the session down.

**Distinguishing feature:** No physical link event in syslog. `show bgp summary` on PE1 shows the session dropped and came back in a short window. CPU utilization on the container is high.

```bash
# Check CPU usage inside the PE1 container
docker exec clab-airgap-noc-pe1 top -bn1 | head -20
docker exec clab-airgap-noc-pe1 vtysh -c "show bgp summary"
```

### 2b. Underlying OSPF/LDP instability propagating to BGP (~25%)
The iBGP session between PE1 and PE2 uses their loopback addresses (10.0.0.11 and 10.0.0.12) as next-hops. If the loopback is not reachable via OSPF/LDP for any reason, the iBGP TCP session drops. This makes what is fundamentally an OSPF/LDP problem appear as a BGP problem.

**Distinguishing feature:** OSPF neighbor loss event on PE1-P1 (eth2) precedes the BGP drop by 1–5 seconds. Check P1 OSPF adjacency state.

```bash
docker exec clab-airgap-noc-p1 vtysh -c "show ip ospf neighbor"
docker exec clab-airgap-noc-pe1 vtysh -c "show mpls ldp neighbor"
```

### 2c. BGP timer misconfiguration or mismatch (~15%)
A recent config push changed the hold-time or keepalive timer on one side of the peering without updating the other. FRR will negotiate down to the lower value, but if one side has a very short timer and the other side is slow to respond, sessions drop.

**Distinguishing feature:** Session drops at a consistent interval (e.g. exactly every 90 seconds). Check timers on both PEs.

```bash
docker exec clab-airgap-noc-pe1 vtysh -c "show bgp neighbors 10.0.0.12" | grep -i timer
docker exec clab-airgap-noc-pe2 vtysh -c "show bgp neighbors 10.0.0.11" | grep -i timer
```

### 2d. Route policy or route-map error causing mass withdrawal (~10%)
A misconfigured route-map or prefix-list on PE1 inadvertently blocks export of VPNv4 prefixes, causing CE2 to see all routes disappear simultaneously — this looks like an ADJCHANGE in the VRF even if the iBGP session itself stays up.

**Distinguishing feature:** `show bgp vrf CUST ipv4 unicast` on PE2 shows no prefixes from CE1, but `show bgp summary` shows PE1 iBGP session as Established.

### 2e. Intentional fault injection by fault_injector.py (~5% in lab)
In the lab context, the `bgp_instability` scenario explicitly issues `vtysh -c 'clear bgp <neighbor>'` commands. If the copilot is called during a scheduled demo run, this is the likely cause.

**Distinguishing feature:** The fault is correlated with a scheduled injection event. Check if `python -m sim.fault_injector --scenario bgp_instability` was run recently.

---

## 3. Remediation Actions (priority order)

### Step 1 — Determine if the iBGP session is currently up or down

```bash
# On PE1: look at the "State/PfxRcd" column
docker exec clab-airgap-noc-pe1 vtysh -c "show bgp summary"

# Expected healthy output (session Established, prefix count > 0):
# Neighbor        V AS   MsgRcvd MsgSent TblVer InQ OutQ Up/Down State/PfxRcd
# 10.0.0.12       4 100     1203    1201      5   0    0 01:23:45        2
```

If the session is Established and prefix count is non-zero, the flap already recovered. Move to Step 3 to prevent recurrence.

If the session shows `Active` or `Idle`, the session is currently down — proceed to Step 2.

### Step 2 — Diagnose and restore the BGP session

```bash
# Step 2a: Is the loopback reachable at the IP level?
docker exec clab-airgap-noc-pe1 ping -c 5 10.0.0.12
# If ping fails: OSPF/LDP is broken. Fix the underlay first (see MPLS runbook).

# Step 2b: Is OSPF up on PE1 → P1?
docker exec clab-airgap-noc-pe1 vtysh -c "show ip ospf neighbor"
# Expect: 10.0.0.21 (P1) in Full state

# Step 2c: If OSPF is up but BGP is still down, do a soft reset
docker exec clab-airgap-noc-pe1 vtysh -c "clear bgp 10.0.0.12 soft"
# Note: `soft` resets only the RIB/policy without dropping the TCP session.
# Use `clear bgp 10.0.0.12` (hard reset) only if soft does not fix it.

# Step 2d: Restart bgpd only as a last resort before escalation
docker exec clab-airgap-noc-pe1 systemctl restart bgpd
# This causes a 10-30 second outage of all BGP sessions on PE1.
```

### Step 3 — Check and align BGP timer configuration

```bash
# Verify timers are consistent on both PEs
docker exec clab-airgap-noc-pe1 vtysh -c "show bgp neighbors 10.0.0.12" | grep -E "Hold|Keepalive"
docker exec clab-airgap-noc-pe2 vtysh -c "show bgp neighbors 10.0.0.11" | grep -E "Hold|Keepalive"

# Standard for this network: hold=90s, keepalive=30s
# To correct a misconfiguration on PE1:
docker exec clab-airgap-noc-pe1 vtysh -c "
  configure terminal
  router bgp 100
   neighbor 10.0.0.12 timers 30 90
  end
  write
"
```

### Step 4 — Apply route dampening if a single prefix is causing churn

If route_churn is high but bgp_flaps are moderate (a specific unstable prefix rather than session flaps):

```bash
# Check which prefix is churning
docker exec clab-airgap-noc-pe1 vtysh -c "show bgp vpnv4 all"

# Apply dampening on PE1 for VPNv4 address family
docker exec clab-airgap-noc-pe1 vtysh -c "
  configure terminal
  router bgp 100
   address-family vpnv4
    bgp dampening 5 750 2000 60
   exit-address-family
  end
  write
"
# Parameters: half-life=5min, reuse=750, suppress=2000, max-suppress=60min
```

### Step 5 — Restore VRF routes after session recovery

After the iBGP session re-establishes, confirm VPNv4 prefixes are present in both PEs' CUST VRFs:

```bash
# CE1's loopback (10.0.0.1/32) should appear on PE2's CUST VRF
docker exec clab-airgap-noc-pe2 vtysh -c "show bgp vrf CUST ipv4 unicast"

# CE2's loopback (10.0.0.2/32) should appear on PE1's CUST VRF
docker exec clab-airgap-noc-pe1 vtysh -c "show bgp vrf CUST ipv4 unicast"

# End-to-end ping: CE1 to CE2 loopback
docker exec clab-airgap-noc-ce1 ping -c 10 10.0.0.2
```

---

## 4. Verification After Fix

```bash
# BGP session must be Established with non-zero prefix count
docker exec clab-airgap-noc-pe1 vtysh -c "show bgp summary"

# No BGP ADJCHANGE events in the last 10 minutes
docker exec clab-airgap-noc-pe1 vtysh -c "show logging" | grep -i adjchange | tail -20

# VPN reachability restored
docker exec clab-airgap-noc-ce1 ping -c 20 -i 0.5 10.0.0.2
# Acceptable: 0% packet loss, <30 ms RTT

# ML risk score should drop below 0.3 for PE1 within 2 minutes of recovery
curl -s http://localhost:8000/api/nodes | python3 -m json.tool | grep -A5 PE1
```

---

## 5. Escalation Criteria

Escalate to Level 2 Network Engineering if any of the following:

- The iBGP session between PE1 and PE2 has dropped more than 3 times in 1 hour without a clear physical cause.
- The session will not re-establish within 5 minutes of a soft clear.
- OSPF adjacency between PE1 and P1 (or P1 and PE2) is also flapping — this is a more serious underlay instability requiring hardware investigation.
- Route dampening is suppressing a prefix that carries critical application traffic (OT/SCADA, management plane).
- The bgp_flaps metric in the telemetry is still rising 10 minutes after the last `clear bgp` command — bgpd may be in a crash-restart loop.

**Include in escalation ticket:** Output of `show bgp summary`, `show bgp neighbors 10.0.0.12`, `show logging` (last 50 lines), and container CPU stats (`top -bn1`).

---

## 6. What NOT To Do

- **Do NOT issue a hard `clear bgp *` (all neighbors) on PE1 without warning.** This resets both the iBGP session to PE2 and the eBGP session to CE1, causing a full VPN outage for all customers on PE1 for 20–60 seconds.
- **Do NOT apply route dampening to the iBGP peer itself** (only to individual prefixes). Dampening an iBGP session address prevents session re-establishment even after the root cause is resolved.
- **Do NOT lower the BGP hold-time below 30 seconds** in this environment. Lower timers mean faster detection but also more false-positive session drops when the FRR container is briefly CPU-busy.
- **Do NOT restart bgpd as a first response.** Try a soft reset first (`clear bgp <neighbor> soft`). A bgpd restart drops all sessions simultaneously.
- **Do NOT confuse eBGP session loss (PE1-CE1) with iBGP session loss (PE1-PE2).** The remediation steps differ significantly. Check `show bgp summary` carefully for which neighbor is affected.
- **Do NOT mark resolved if path_asymmetry is still elevated.** A recovered BGP session with asymmetric paths indicates partial route convergence — some traffic may still take a suboptimal path and the fault class prediction may re-trigger.

---

## 7. Related Fault Classes

- **mpls_degradation**: OSPF/LDP underlay issues directly cause iBGP session drops. If bgp_instability and mpls_degradation alerts co-occur, fix the MPLS underlay first.
- **congestion**: Severe congestion on PE1 can starve bgpd keepalives. If congestion and bgp_instability alerts co-occur within seconds of each other, address the congestion first.
- **policy_drift**: A misconfigured route-map can cause route churn without a BGP session drop. If bgp_flaps count is low but route_churn is high, look at policy changes.
