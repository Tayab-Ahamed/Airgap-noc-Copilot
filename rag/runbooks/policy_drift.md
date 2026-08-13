# Runbook: QoS / ACL Policy Drift (fault class: policy_drift)

**ML fault class ID:** 4  
**Default target node:** CE2 (datacenter-side customer edge)  
**Monitored features:** qos_violations, acl_mismatch  
**Ramp time to impact:** ~150 seconds of building precursors before application-visible degradation

---

## 1. Symptom Recognition

Policy drift is the most operationally ambiguous of the four fault classes. Unlike congestion or MPLS degradation, there is no single hard-threshold alert that triggers — the impairment accumulates as a misconfigured QoS class or ACL entry silently mishandles traffic over time.

| Time relative to impact | Signal | Threshold warranting action |
|---|---|---|
| T-150 s | qos_violations metric rising on CE2 | Any non-zero rate sustained >90 s |
| T-120 s | acl_mismatch metric rising (hit counters on unexpected permit/deny entries) | Any non-zero rate sustained >60 s |
| T-90 s | Specific traffic class (DSCP EF or AF) begins dropping | Drop rate >0.5% on a previously clean class |
| T-60 s | TCP retransmit rate rising for application traffic traversing CE2 | >1% retransmit rate |
| T-0 | User reports: latency-sensitive applications degraded; DSCP re-marking visible | MOS <3.5 for voice; application response >200 ms above baseline |

**Key diagnostic signals — compare against the golden config:**

```bash
# On CE2: show current running config
docker exec clab-airgap-noc-ce2 vtysh -c "show running-config"

# Show ACL hit counters (acl_mismatch shows up as unexpected ACE hits)
docker exec clab-airgap-noc-ce2 vtysh -c "show ip access-lists"

# Show interface QoS policy (or its absence)
docker exec clab-airgap-noc-ce2 vtysh -c "show interface eth1"
```

**What to look for in the diff against golden config:**

- A `no service-policy` on CE2 eth1 egress (QoS policy removed).
- A changed ACL sequence that now has a `deny` entry where `permit` is expected, or vice versa.
- A changed DSCP re-marking rule (traffic that should be marked EF is now marked BE/default).
- A missing or altered prefix-list entry that changes which routes are accepted from PE2.

**Change window correlation:** Most policy_drift events trace to a change-control operation. Pull the change log for CE2 and look for any config push in the 5 minutes before the alert.

---

## 2. Root Causes (ranked by probability)

### 2a. Automated config push applied an incorrect template (~45%)
A configuration management system (Ansible, NAPALM, Netmiko) pushed a base template to CE2 that does not include the site-specific QoS policy or ACL. The site-generic template is syntactically valid but functionally wrong for this node.

**Distinguishing feature:** The running config on CE2 is valid FRR config but differs from the golden config in specific policy sections. The change timestamp on CE2 correlates with a scheduled automation run.

**Why the ML model catches it:** Automated pushes often run at consistent times (e.g. midnight maintenance window). The model sees qos_violations and acl_mismatch start rising simultaneously within seconds of each other, which is the template-push signature.

### 2b. Manual configuration error by an operator (~25%)
An operator SSHed to CE2 for an unrelated change and accidentally removed or modified a QoS policy or ACL entry.

**Distinguishing feature:** Change log shows an interactive session, not an automation run. The operator may not have realized the impact if the change was in a different section of the config.

### 2c. FRR daemon restart cleared transient in-memory state (~15%)
When FRR's zebra or a specific protocol daemon restarts, some policy state that was applied via the vtysh API but not committed to `frr.conf` is lost. This can happen after a container restart or a bgpd/ospfd crash-recovery.

**Distinguishing feature:** The running config looks correct (`show running-config`) but `show ip access-lists` shows zero hit counters — the ACL is configured but not actually enforced. Restart zebra to reload from `frr.conf`.

```bash
# Check if configuration in frr.conf matches running config
docker exec clab-airgap-noc-ce2 diff \
  <(vtysh -c "show running-config") \
  /etc/frr/frr.conf
```

### 2d. Route-map or prefix-list version mismatch after BGP reconvergence (~10%)
After a BGP session reset, route-maps are re-evaluated. If a route-map references a prefix-list that was updated between the original session establishment and the reset, previously-permitted prefixes may now be denied, causing traffic to be dropped at the CE.

**Distinguishing feature:** Policy drift alert co-occurs with a bgp_instability event on PE2. The acl_mismatch metric rises specifically after the BGP session re-establishes.

### 2e. netem qdisc from a fault injection test not cleared (~5% in lab)
The `policy_drift` scenario in `sim/fault_injector.py` applies a `tc netem reorder + loss` qdisc on CE2 eth1. If the lab operator forgot to run `--scenario clear` after a test, the impairment persists.

**Distinguishing feature:** Running `tc qdisc show dev eth1` on CE2 shows a netem qdisc with non-default parameters.

---

## 3. Remediation Actions (priority order)

### Step 1 — Confirm the specific drift type: QoS or ACL

```bash
# Check for QoS policy on CE2 eth1 egress
docker exec clab-airgap-noc-ce2 vtysh -c "show interface eth1" | grep -i policy

# Check ACL hit counters — which entries have unexpected hits?
docker exec clab-airgap-noc-ce2 vtysh -c "show ip access-lists"

# Check for any active tc qdisc (lab netem injection)
docker exec clab-airgap-noc-ce2 tc qdisc show dev eth1
docker exec clab-airgap-noc-ce2 tc qdisc show dev eth2
```

If `tc qdisc show` returns a `netem` entry: this is lab injection — go to Step 2a.  
If QoS policy is missing from `show interface`: go to Step 2b.  
If ACL entries differ from golden: go to Step 2c.

### Step 2a — Clear lab injection qdisc (lab environment only)

```bash
docker exec clab-airgap-noc-ce2 tc qdisc del dev eth1 root 2>/dev/null || true
docker exec clab-airgap-noc-ce2 tc qdisc del dev eth2 root 2>/dev/null || true
# Verify cleared:
docker exec clab-airgap-noc-ce2 tc qdisc show dev eth1
# Expected: only the default 'pfifo_fast' or 'noqueue' qdisc
```

### Step 2b — Re-apply the QoS policy from the golden config

```bash
# If the QoS policy was removed from CE2 eth1:
docker exec clab-airgap-noc-ce2 vtysh -c "
  configure terminal
  ! Re-apply the standard QoS class for voice/real-time traffic
  class-map match-any REALTIME
   match dscp ef
  !
  class-map match-any BUSINESS
   match dscp af31 af32 af33
  !
  policy-map QOS-CE2-EGRESS
   class REALTIME
    police rate 30% burst 10kb exceed-action drop
   class BUSINESS
    police rate 40% burst 20kb exceed-action drop
   class class-default
    fair-queue
  !
  interface eth1
   service-policy output QOS-CE2-EGRESS
  end
  write
"
```

**Note:** Adjust the class percentages to match your site's contracted rates. The above is a representative example; always refer to the site-specific golden config file before applying.

### Step 2c — Restore the ACL from the golden config

```bash
# Step 1: Show current ACLs on CE2
docker exec clab-airgap-noc-ce2 vtysh -c "show ip access-lists"

# Step 2: Compare with the expected golden ACL
# The golden config for CE2 should be in your CMDB or version-controlled in Git.
# In this lab, the reference is /etc/frr/frr.conf (the bind-mounted golden).

# Step 3: Re-apply the correct ACL
docker exec clab-airgap-noc-ce2 vtysh -c "
  configure terminal
  ! Example: restore a permit for the branch subnet
  ip access-list extended FROM-CE2
   permit ip 192.168.2.0 0.0.0.255 any
   permit ip 10.0.0.2 0.0.0.0 any
   deny ip any any log
  !
  interface eth1
   ip access-group FROM-CE2 in
  end
  write
"
```

### Step 3 — Commit the corrected config to persistent storage

FRR's `write` (or `write memory`) saves the running config back to `/etc/frr/frr.conf`. Confirm:

```bash
docker exec clab-airgap-noc-ce2 vtysh -c "write memory"
# Expected: Note: configuration was successfully saved to /etc/frr/frr.conf

# Verify the file was updated
docker exec clab-airgap-noc-ce2 cat /etc/frr/frr.conf | grep -A10 "policy-map\|access-list"
```

### Step 4 — Verify traffic is no longer misdropped or reordered

```bash
# From CE1 (branch): ping CE2's customer LAN stub
docker exec clab-airgap-noc-ce1 ping -c 50 -i 0.1 192.168.2.1 2>/dev/null || \
  docker exec clab-airgap-noc-ce1 ping -c 50 -i 0.1 10.0.0.2

# Verify zero ACL drops on the intended permit entries
docker exec clab-airgap-noc-ce2 vtysh -c "show ip access-lists"
# The DENY entry hit counter should be zero (or very low) after fix

# Confirm qos_violations and acl_mismatch metrics have dropped to zero
# in the backend telemetry feed
curl -s http://localhost:8000/api/nodes | python3 -m json.tool | grep -A5 CE2
```

---

## 4. Verification After Fix

All of the following should be true before marking the alert resolved:

```bash
# 1. No netem qdisc on CE2 (lab only)
docker exec clab-airgap-noc-ce2 tc qdisc show | grep netem
# Expected: no output

# 2. QoS policy active on CE2 eth1
docker exec clab-airgap-noc-ce2 vtysh -c "show interface eth1" | grep "Service-policy"
# Expected: Service-policy output: QOS-CE2-EGRESS (or site-equivalent)

# 3. ACL hit counters: permit entries have hits, deny entry has zero or near-zero
docker exec clab-airgap-noc-ce2 vtysh -c "show ip access-lists"

# 4. Running config matches frr.conf (no transient state divergence)
docker exec clab-airgap-noc-ce2 diff \
  <(vtysh -c "show running-config") /etc/frr/frr.conf | wc -l
# Expected: 0 (no differences)

# 5. ML risk score for CE2 below 0.3 for 5 consecutive minutes
curl -s http://localhost:8000/api/nodes | python3 -m json.tool | grep -A5 CE2
```

---

## 5. Escalation Criteria

Escalate to Level 2 / Configuration Management team if any of the following:

- The golden config for CE2 is itself incorrect (i.e. the policy was never right) — requires a proper change-control review, not a NOC fix.
- The policy_drift alert recurs within 30 minutes of re-applying the golden config — suggests an automated system is re-pushing the broken template.
- ACL changes have inadvertently blocked management-plane traffic (SSH access to CE2 lost, or SNMP polling failing).
- The DSCP markings on traffic exiting CE2 do not match expectations after re-applying QoS — may indicate a PE2 ingress re-marking policy overriding the CE's markings.
- You cannot identify what the correct golden config should be — this requires a network engineer review, not a NOC fix.

---

## 6. What NOT To Do

- **Do NOT apply a generic "wipe and reload" of FRR config** to fix a small policy error. A full config reload causes a brief outage as all FRR daemons restart.
- **Do NOT use `no ip access-group` to temporarily remove an ACL** while investigating. This removes all access control from the interface, not just the broken entry.
- **Do NOT commit a fix without verifying the frr.conf golden source.** Applying a change from memory or a verbal description may re-introduce the original error or introduce a new one.
- **Do NOT ignore a policy_drift alert because the user-visible impact is "minor reordering."** Packet reordering degrades TCP performance (spurious retransmits) and triggers head-of-line blocking in application queues; the downstream effect is larger than it appears from raw metrics.
- **Do NOT attempt to fix the qos_violations metric by raising the QoS policer rate.** Raising the police rate masks the symptom but does not fix the underlying misconfiguration and may cause over-subscription.
- **Do NOT make config changes on CE2 during business hours without a change-control ticket** even if the change is a restoration of the golden config. Incorrect restorations during production hours have caused outages.
- **Do NOT clear the BGP session on PE2** to try to "refresh" routes after an ACL change. The ACL operates on the forwarding plane, not the control plane; clearing BGP is irrelevant and causes a VPN outage.

---

## 7. Related Fault Classes

- **congestion**: A stripped QoS policy on CE2 allows un-policed bursts that propagate toward PE2 and PE2's eth2-PE-P link. A policy_drift event on CE2 may trigger a secondary congestion event on PE2.
- **bgp_instability**: A misconfigured prefix-list (a specific type of policy drift) can cause BGP route withdrawals that look like bgp_instability. If both alert types co-occur on CE2 and PE2, start with policy_drift.
- **mpls_degradation**: MTU changes pushed as part of a policy template update on CE2 can introduce fragmentation issues in the MPLS path. The `acl_mismatch` metric does not capture MTU changes directly; look at interface MTU in the config diff.
