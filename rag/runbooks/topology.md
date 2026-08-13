# Network Topology Reference — Airgap NOC Copilot

**Document type:** Internal NOC reference (not customer-facing)  
**Applies to:** airgap-noc containerlab topology, FRR v8.4.0  
**Primary source of truth:** `sim/topology.clab.yml` and `sim/configs/`

---

## 1. Site and Node Overview

This network implements a classic three-tier MPLS VPN topology connecting two sites: a **branch site** and a **datacenter site**, joined via a two-node MPLS core.

```
Branch Site                  MPLS Core                Datacenter Site
-----------                  ---------                ---------------
                             
  CE1 ─── PE1 ─── P1 ─── PE2 ─── CE2
  
  AS 65001  AS 100   (transit)  AS 100    AS 65002
```

**Five nodes, four links, linear topology — there are no redundant paths in the default deployment.**

---

## 2. Node Roles and Responsibilities

### CE1 — Customer Edge, Branch Site

| Attribute | Value |
|---|---|
| Container name | `clab-airgap-noc-ce1` |
| Loopback | 10.0.0.1/32 |
| BGP AS | 65001 |
| eBGP peer | PE1 (10.0.12.2) |
| eth1 link | CE1 ↔ PE1 (10.0.12.1/30) |
| FRR daemons | zebra, bgpd, bfdd, staticd |
| IPSec role | Tunnel initiator/responder to CE2 |

CE1 is the branch-side customer edge. It does **not** run OSPF or LDP — those protocols are internal to the provider network. CE1 peers with PE1 via eBGP to exchange the branch site's prefixes (loopback 10.0.0.1/32, stub LAN 192.168.1.0/24) and to receive the datacenter routes.

CE1 also terminates the SD-WAN IPSec overlay tunnel to CE2. The tunnel uses CE1's loopback (10.0.0.1) as the local endpoint, making it PE-failover-resilient. The loopback is reachable through the BGP/MPLS VPN path.

**Operational note:** CE1 has no alternate path to PE1. If the CE1-PE1 link fails, the branch site loses all connectivity. This is the most common single point of failure for branch outages.

---

### PE1 — Provider Edge, Branch-Facing

| Attribute | Value |
|---|---|
| Container name | `clab-airgap-noc-pe1` |
| Loopback | 10.0.0.11/32 |
| BGP AS | 100 |
| iBGP peer | PE2 (10.0.0.12), session uses loopbacks |
| eBGP peer in VRF | CE1 (10.0.12.1) |
| eth1 link | PE1 ↔ CE1 (10.0.12.0/30) — customer-facing, in VRF CUST |
| eth2 link | PE1 ↔ P1 (10.0.23.0/30) — MPLS core-facing |
| VRF | CUST (VNI 100, RT 100:100 import/export) |
| FRR daemons | zebra, ospfd, ldpd, bgpd, bfdd, staticd |

PE1 is the most functionally complex node in the topology. It sits at the boundary between the customer network (VRF CUST) and the MPLS provider core, performing the following functions simultaneously:

1. **OSPF area 0** on eth2 and loopback — distributes reachability of 10.0.0.11/32 so the iBGP session to PE2 can be established using loopbacks.
2. **LDP** on eth2 — exchanges MPLS label bindings with P1. PE1's loopback (10.0.0.11/32) is allocated a label and that label is distributed to PE2 via P1, enabling the PE1-PE2 LSP.
3. **iBGP VPNv4** to PE2 (10.0.0.12) — carries the customer VPN routes (with RD and RT attributes) across the MPLS backbone.
4. **eBGP in VRF CUST** to CE1 — receives the branch site's prefixes and redistributes them into the VPNv4 table with RD 100:100.

**Failure impact:** If PE1 goes down, the branch site loses all VPN connectivity, OSPF adjacency on the P1-PE1 link is lost, and if P1 has no alternate path, the PE1-PE2 LSP is torn down.

---

### P1 — Provider Core Router

| Attribute | Value |
|---|---|
| Container name | `clab-airgap-noc-p1` |
| Loopback | 10.0.0.21/32 |
| BGP | None (no BGP on P1) |
| eth1 link | P1 ↔ PE1 (10.0.23.0/30) |
| eth2 link | P1 ↔ PE2 (10.0.34.0/30) |
| FRR daemons | zebra, ospfd, ldpd, bfdd, staticd |

P1 is the **only transit router** between the branch and datacenter sides of the MPLS network. It participates in OSPF area 0 on both interfaces and distributes MPLS labels via LDP to both PE1 and PE2.

P1 has **no customer VRF** and **no BGP** — it sees only the provider infrastructure addresses (loopbacks and link addresses). Customer (VPN) traffic crosses P1 as MPLS-labelled frames; P1 never inspects the payload.

**Critical operational implication:** Because P1 is the only core node, there is **no redundancy** in the MPLS backbone. A P1 failure or a configuration error on P1 (wrong OSPF area, missing LDP interface, MTU mismatch) simultaneously brings down all PE-PE LSPs and therefore all VPN traffic.

**Fault injection target:** MPLS degradation scenarios primarily impair P1's links because that is where label-switched traffic can be disrupted without touching customer-facing ports.

---

### PE2 — Provider Edge, Datacenter-Facing

| Attribute | Value |
|---|---|
| Container name | `clab-airgap-noc-pe2` |
| Loopback | 10.0.0.12/32 |
| BGP AS | 100 |
| iBGP peer | PE1 (10.0.0.11), session uses loopbacks |
| eBGP peer in VRF | CE2 (10.0.45.2) |
| eth1 link | PE2 ↔ P1 (10.0.34.0/30) — MPLS core-facing |
| eth2 link | PE2 ↔ CE2 (10.0.45.0/30) — customer-facing, in VRF CUST |
| VRF | CUST (VNI 100, RT 100:100 import/export — same RT as PE1) |
| FRR daemons | zebra, ospfd, ldpd, bgpd, bfdd, staticd |

PE2 is the datacenter-side mirror of PE1. It performs the same four functions (OSPF, LDP, iBGP VPNv4, eBGP in VRF CUST) but faces CE2 instead of CE1.

The **same route-target (100:100)** is used on both PE1 and PE2, so routes exported by PE1's VRF CUST are automatically imported by PE2's VRF CUST, and vice versa. This is the mechanism that makes CE1 and CE2 able to reach each other through the VPN.

**Default fault injection target for mpls_degradation:** PE2 eth1 (the P1-facing link) is where the `mpls_degradation` scenario applies `tc netem loss + jitter`, simulating degradation of the labelled-frame path from the DC side.

**Default fault injection target for policy_drift:** CE2 (not PE2) is the direct target, but CE2's misconfiguration affects what traffic PE2 receives on its eth2 (customer-facing) interface.

---

### CE2 — Customer Edge, Datacenter Site

| Attribute | Value |
|---|---|
| Container name | `clab-airgap-noc-ce2` |
| Loopback | 10.0.0.2/32 |
| BGP AS | 65002 |
| eBGP peer | PE2 (10.0.45.1) |
| eth1 link | CE2 ↔ PE2 (10.0.45.2/30) |
| FRR daemons | zebra, bgpd, bfdd, staticd |
| IPSec role | Tunnel responder to CE1 |
| Stub LAN | 192.168.2.0/24 (datacenter LAN, static route on CE2) |

CE2 is the datacenter-side customer edge. It is the most likely target of `policy_drift` faults because datacenter devices tend to be in scope for automated configuration management systems, which are a common source of accidental policy changes.

CE2 is also the **IPSec responder** for the SD-WAN overlay tunnel from CE1. It listens for IKEv2 initiation from CE1 (10.0.0.1) and, once the tunnel is established, forwards all inter-site traffic through the encrypted overlay.

---

## 3. Link and Address Plan

| Link | Subnet | Left endpoint | Right endpoint | Purpose |
|---|---|---|---|---|
| CE1 eth1 ↔ PE1 eth1 | 10.0.12.0/30 | CE1: 10.0.12.1 | PE1: 10.0.12.2 | Customer access, in VRF CUST on PE1 |
| PE1 eth2 ↔ P1 eth1 | 10.0.23.0/30 | PE1: 10.0.23.1 | P1: 10.0.23.2 | MPLS core uplink, OSPF+LDP |
| P1 eth2 ↔ PE2 eth1 | 10.0.34.0/30 | P1: 10.0.34.1 | PE2: 10.0.34.2 | MPLS core downlink, OSPF+LDP |
| PE2 eth2 ↔ CE2 eth1 | 10.0.45.0/30 | PE2: 10.0.45.1 | CE2: 10.0.45.2 | Customer access, in VRF CUST on PE2 |

**Loopback addresses (used as BGP router-IDs and LDP transport addresses):**

| Node | Loopback | OSPF advertised | LDP transport |
|---|---|---|---|
| CE1 | 10.0.0.1/32 | No | No |
| PE1 | 10.0.0.11/32 | Yes (area 0) | Yes |
| P1 | 10.0.0.21/32 | Yes (area 0) | Yes |
| PE2 | 10.0.0.12/32 | Yes (area 0) | Yes |
| CE2 | 10.0.0.2/32 | No | No |

**Customer stub LANs (not physically present in containerlab, used in IPSec config only):**

- Branch: 192.168.1.0/24 (CE1 side)
- Datacenter: 192.168.2.0/24 (CE2 side)

---

## 4. Control Plane Architecture

### OSPF Area 0 (provider core only)

OSPF runs only within the provider network — PE1, P1, and PE2 are in area 0. CE nodes do not participate in OSPF.

```
PE1 (10.0.0.11) ──── P1 (10.0.0.21) ──── PE2 (10.0.0.12)
         OSPF area 0, all links
```

**OSPF purpose in this network:** Distribute loopback reachability so that LDP and iBGP can use loopbacks as stable identifiers. OSPF does NOT carry customer routes.

### LDP (Label Distribution Protocol)

LDP runs on the same interfaces as OSPF: PE1-P1 (eth2/eth1) and P1-PE2 (eth2/eth1). Each node's loopback address is the LDP router-ID and transport address.

**LDP session matrix:**

| Session | Local transport | Remote transport |
|---|---|---|
| PE1 ↔ P1 | 10.0.0.11 | 10.0.0.21 |
| P1 ↔ PE2 | 10.0.0.21 | 10.0.0.12 |

LDP distributes labels for all prefixes in the OSPF database, including the CE loopbacks once they are redistributed via BGP → OSPF (if configured) or directly via OSPF. In the default config, only provider loopbacks are in OSPF; customer prefixes travel as VPNv4 with MPLS labels allocated by the iBGP VPNv4 process.

### BGP VPNv4 (PE-PE iBGP)

A single iBGP session runs between PE1 (10.0.0.11) and PE2 (10.0.0.12) using their loopbacks as the `update-source`. Both PEs are in AS 100.

**Route-target and RD policy:**

| Node | VRF | RD | RT import | RT export |
|---|---|---|---|---|
| PE1 | CUST | 100:100 | 100:100 | 100:100 |
| PE2 | CUST | 100:100 | 100:100 | 100:100 |

With matching RT import and export on both PEs, every prefix that PE1 exports (CE1's loopback, 10.0.0.1/32) is imported by PE2 into VRF CUST and becomes reachable from CE2, and vice versa.

### eBGP CE-PE sessions

| Session | CE AS | PE AS | CE address | PE address |
|---|---|---|---|---|
| CE1 ↔ PE1 | 65001 | 100 | 10.0.12.1 | 10.0.12.2 |
| CE2 ↔ PE2 | 65002 | 100 | 10.0.45.2 | 10.0.45.1 |

These are standard eBGP sessions running inside VRF CUST on each PE. The PE receives the CE's loopback and stub LAN prefixes, tags them with the VRF's RD and RT, and redistributes them into VPNv4.

---

## 5. Data Plane: MPLS Label-Switched Path

When CE1 pings CE2's loopback (10.0.0.2), the packet path is:

1. **CE1 eth1** → PE1 eth1: plain IP packet (no label), CE1 does not do MPLS.
2. **PE1 (ingress LER)**: Looks up 10.0.0.2 in VRF CUST. Finds a VPNv4 route with next-hop 10.0.0.12 (PE2's loopback). Applies two labels:
   - Inner label: VPN label allocated by PE2 for the CUST VRF prefix.
   - Outer label: LDP transport label for reaching 10.0.0.12 via P1.
3. **P1 (transit LSR)**: Swaps the outer transport label and forwards to PE2 eth1.
4. **PE2 (egress LER)**: Pops both labels (penultimate-hop popping may remove the outer label at P1). Looks up the inner VPN label → VRF CUST → forwards to CE2 via eth2.
5. **PE2 eth2** → CE2 eth1: plain IP packet again.

**Total MPLS label stack depth: 2 labels** (VPN label + transport label) on the PE1→P1 segment.  
**After PHP (penultimate hop popping):** P1 pops the transport label, delivering single-labelled frames to PE2 (VPN label only).

---

## 6. SD-WAN IPSec Overlay

In addition to the MPLS VPN data plane, CE1 and CE2 run an IKEv2/IPSec tunnel directly between their loopbacks:

```
CE1 (10.0.0.1) ═══════ IKEv2 + ESP (AES-256-SHA-256) ═══════ CE2 (10.0.0.2)
                    transported over the MPLS VPN path
```

**The IPSec tunnel rides inside the MPLS VPN**, using the VPN-forwarded loopback addresses as endpoints. This means:

- The MPLS VPN must be working for the IPSec tunnel to establish.
- An MPLS path failure will cause the IPSec tunnel to tear down after the dead-peer-detection timeout.
- The IPSec tunnel provides end-to-end encryption even though the MPLS backbone is a trusted provider network (defense in depth for govt/sensitive data environments).

**PSK location:** `/etc/ipsec.secrets` on CE1 and CE2 (bind-mounted from `sim/configs/ce{1,2}/ipsec.secrets`). Use certificate-based auth in production.

---

## 7. Telemetry and Monitoring Points

The ML engine consumes the following telemetry features per node per minute:

| Feature | Source | Normal range | Fault sensitivity |
|---|---|---|---|
| if_utilization | Interface egress bit rate ÷ link capacity | 20–50% | Congestion |
| latency_ms | TWAMP or ICMP RTT ÷ 2 | 10–20 ms | Congestion, MPLS degradation |
| jitter_ms | P95–P50 one-way delay variation | 1–3 ms | MPLS degradation |
| queue_drops | Interface output queue drop counter rate | 0 | Congestion |
| tunnel_loss | IPSec/MPLS tunnel packet loss % | <0.1% | MPLS degradation |
| bgp_flaps | BGP ADJCHANGE event count per min | 0 | BGP instability |
| route_churn | BGP prefix withdrawal+readvertise rate | <0.5/min | BGP instability |
| path_asymmetry | Forward-path ≠ reverse-path metric | <0.2 | BGP instability |
| rekey_anomaly | IKE rekey event rate anomaly | 0 | MPLS degradation |
| qos_violations | QoS policer drop count rate | 0 | Policy drift |
| acl_mismatch | ACL deny hit count rate | 0 | Policy drift |

**Telemetry collection:** In the lab, synthetic data is generated by `ml/synthetic_data.py`. In production, Telegraf (`telemetry/telegraf.conf`) collects from the FRR containers via GNMI or SNMP and writes to the backend stream.

---

## 8. Fault Class to Node Mapping

The ML model assigns fault classes to individual nodes. The following matrix shows which faults are associated with which nodes at their default injection points:

| Fault class | Primary node | Secondary (affected) nodes |
|---|---|---|
| congestion | PE1 | CE1 (source burst), P1 (downstream effect) |
| bgp_instability | PE1 | PE2 (peer loses session), CE1/CE2 (lose VPN routes) |
| mpls_degradation | PE2 | P1 (loss injected here), CE1/CE2 (end-user impact) |
| policy_drift | CE2 | PE2 (receives misconfigured traffic from CE2) |

**Important:** The copilot reports the fault with the node where the ML model predicts the originating cause, not necessarily the node where the user experiences the impact. A `mpls_degradation` alert on PE2 means the degradation is originating on or near PE2's upstream path, even if the user on CE1 is the one experiencing the drop.

---

## 9. Common Operator Questions

**Q: Why is CE2 flagged for policy_drift but the problem feels like a network issue, not a customer config issue?**  
A: CE2 is a customer edge router under customer (or customer-delegated) management. Policy changes on CE2 — including automated config pushes from a CMDB system — are outside the provider's change-control window. The provider NOC's role is to detect the drift, inform the customer, and assist with remediation. The provider does NOT change CE2 config without customer authorization.

**Q: Can we add a redundant P1 or alternate PE path?**  
A: The default topology is a single linear chain for simplicity. Adding a second P node (P2) or a direct PE1-PE2 link requires updating `topology.clab.yml` and the FRR configs. This is a topology design change, not a NOC remediation action.

**Q: How long does OSPF take to reconverge after a link failure?**  
A: With default FRR timers (hello=10s, dead=40s), OSPF detects a neighbor loss in 40 seconds. BFD (bfdd, enabled on all nodes) can reduce this to sub-second detection with appropriate BFD timer configuration. LDP re-establishes after OSPF converges (~10–30 s additional). Total reconvergence: 40–70 seconds with default timers, <5 seconds with BFD sub-second timers.

**Q: What is the difference between a BGP reset and an OSPF adjacency loss?**  
A: OSPF runs on the link layer (eth2 on PEs) and keeps adjacency as long as the physical link is up. BGP runs on TCP between loopback addresses. You can have an OSPF adjacency with P1 but a BGP session to PE2 that is down (e.g. because P1 is forwarding OSPF but dropping TCP/179 packets). Conversely, you can have BGP up but MPLS failing (OSPF routes the control plane but LDP has a different issue). Always check both planes independently.

**Q: Where do I find the FRR configuration files for each node?**  
A: In the repository at `sim/configs/<node>/frr.conf`. These are bind-mounted into each container at `/etc/frr/frr.conf` when `containerlab deploy` runs. The running config (which may differ if changes were made via vtysh without `write memory`) can be viewed with `vtysh -c "show running-config"` inside the container.
