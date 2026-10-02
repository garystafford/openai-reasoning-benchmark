# Complex reasoning benchmark: 27 distinct tasks

Version `complex-main-v4` · reasoning dataset `unique-reasoning-v4.1` · October 1, 2026

**Nine domains, three different complex tasks per domain, 27 tasks total.** The main score contains no changed-number or reworded variants. Each model/reasoning configuration receives the same source packs and rules. Three repetitions measure response variability; they do not create new questions.

The portable [JSONL dataset](../benchmarks/main/dataset.jsonl) contains one row per task: ID, domain, primary reasoning mechanism, system/user messages, strict Structured Outputs schema in `text.format`, expected answer, and provenance metadata. The [main manifest](../evals/main-matrix.json) controls selection. Schemas use declared types and input IDs; expected answers remain separate from model-visible messages and schemas. Source packs are fictional and self-contained; no browsing or tools are required during evaluation.

The v4.1 invoice prompt explicitly separates quantity shortfall from payment
blocked by a price dispute. `held_equivalents` remains the invoiced quantity minus
quantity-matched equivalents, independent of price; a price mismatch makes the
line payable zero. `deduplicated_record_ids` contains only IDs occurring more
than once within receipt, return, or credit rows. Expected answers and output
schemas are unchanged. Revised prompts are a new evaluation condition and must
not be pooled with the preceding sweep.

## Domain and question matrix

| Domain | Task 1 | Task 2 | Task 3 | Questions |
| --- | --- | --- | --- | ---: |
| Operations planning | Disruption response: optimize fulfillment under inventory/deadline constraints | Production job shop: weighted tardiness with two machines and maintenance | Warehouse queue: event ordering, worker pauses, and release holds | 3 |
| Procurement | Sourcing network: supplier bundles, timed stock, and diversification | Contingent supplier: minimize worst-case regret across disruptions | Invoice match: substitutions, returns, duplicate credits, and price disputes | 3 |
| Liquidity | Liquidity facilities: cash-ledger reconciliation and staged financing | Treasury netting: currency-specific pools, restricted cash, and gross obligations | Refinance waterfall: circular covenant cap, capitalized fees, and financing limits | 3 |
| Financial diligence | Diligence reconciliation: scoped adjustments, covenant headroom, missing evidence | Revenue cohort bridge: retention, churn, acquisition scope, and FX | Cash earnings quality: cash/noncash addbacks and maintenance reclassification | 3 |
| Transformation | Transformation roadmap: dependencies, overlapping benefits, resources, and NPV | Rollout impact estimate: standardized difference-in-differences and causal uncertainty | Automation routing: asymmetric error costs, review capacity, and policy gates | 3 |
| Engagement delivery | Engagement capacity: multi-week skills, reservations, and independent review | Work-package assignment: indivisible assignments and dependency handoff costs | Engagement rescue: sunk costs, signed scope, incompatibilities, and expected payout | 3 |
| Incident response | Incident handoff: normalized attempts, incomplete evidence, and safe retries | Fault hypothesis constraints: minimal explanations versus confirmed faults | Lease fencing recovery: clock offsets, stale tokens, and safe replay prefixes | 3 |
| Disaster recovery | Recovery chain: verified backup dependencies and resource scheduling | Consistent restore cut: cross-store atomicity, referential integrity, and RPO | Regional failover: correlated fault domains, latency, and resilient capacity | 3 |
| Release engineering | Canary segment analysis: fixed-mix errors and release gates | Expand/contract rollout: state transitions and prefix safety | Version contract resolution: capability compatibility and migration costs | 3 |
| **Total** | | | | **27** |

All tasks are labeled complex by design: they require linked steps or interacting constraints. Difficulty has not yet been calibrated empirically. The three enterprise/consulting/technical profiles each contain nine tasks and are organizational views, not additional domains.


## Research basis

The design uses representative professional work as a goal, following the motivation of [GDPval](https://openai.com/index/gdpval/), and explicit numerical programs and structured/text evidence from [FinQA](https://aclanthology.org/2021.emnlp-main.300/) and [TAT-QA](https://aclanthology.org/2021.acl-long.254/). No benchmark questions were copied.

Scheduling and resource constraints draw on the [OR-Tools job-shop formulation](https://developers.google.com/optimization/scheduling/job_shop). Release and incident cases draw on [Google SRE canarying](https://sre.google/workbook/canarying-releases/) and [distributed periodic scheduling](https://sre.google/sre-book/distributed-periodic-scheduling/). Recovery integrity draws on [NIST contingency planning](https://csrc.nist.gov/pubs/sp/800/34/r1/final), [PostgreSQL transaction isolation](https://www.postgresql.org/docs/current/transaction-iso.html), and [event-sourcing patterns](https://learn.microsoft.com/en-us/azure/architecture/patterns/event-sourcing). These sources inform task structure; all numerical inputs and operative rules are defined in each fictional source pack. They do not validate the answer keys.

## Answer validation and reasoning ledgers

The 18 added tasks have static answer keys and separate executable specifications. Builders render prompts and exports; they never rewrite goldens. Verification derives answers from source data, checks schema/metadata/coverage, and grades every key through the actual assertion. Tests include independent quantity enumeration for minimax procurement, financial bridge identities, invalid operational plans, input perturbations, missing evidence, ordered sequences, and source reordering. All 27 retained goldens are checked against the executable solvers and deterministic grader. This is programmatic verification, not independent human expert review.

Key calculations for the added tasks:

| Task | Checked answer / decisive reasoning |
| --- | --- |
| Production job shop | CUT starts A3/B0/C6/D2; FINISH A8/B2/C12/D4. Completions A12/B4/C15/D6. Penalty 2×20+1×10=$50; makespan15. Enumerate both machine orders and apply earliest feasible starts, maintenance, then stipulated tie-breaks. Earlier feasible starts dominate intentional delay for these nonnegative tardiness objectives. |
| Warehouse queue | PICK A0/B7/C9/D12/E13; PACK A4/B12/C14/D15/E19. Completion A7/B14/C15/D19/E21; lateness 0+5+2+5+3=15. B's hold delays B, not the entire pack queue. |
| Contingent supplier | Pair A/B net values normal1000/outage540/delay1000. Scenario optima1020/810/1020; regrets20/270/20. Worst regret270, mean84667 cents. Exhaustively search all pairs; independent tests enumerate exercise quantities. |
| Invoice match | P1 received6+(8−2)/2=9 equivalents: 9×100−50=$850. P2 has price dispute:0. P3 pays4×80=$320. Total1170; duplicate C1/R2 counted once. |
| Treasury netting | USD positions A−60/B30/C30; EUR A−30/B30. Funding A:USD45, A:EUR20, C:EUR25; others0. Reporting USD total45+(20+25)×6/5=99. Restricted cash and pool receivables cannot eliminate gross outside-pool obligations. |
| Refinance waterfall | Synergy cap s=(100000+s)/5 yields25000; leverage limit375000. Signed quote B, principal360000, fee10000, gross debt370000, interest22200, distribution150000. Larger principal violates leverage. |
| Revenue cohort bridge | Opening250000+expansion30000−contraction25000−churn50000+new/reactivated70000=organic275000. Acquired70000; closing-FX effect−5000; reported340000. Cohort ending205000 gives NDR8200bps; capped retention175000 gives GDR7000bps. |
| Cash earnings quality | Adjusted EBITDA250000; operating cash200000+20000−15000−10000−35000−20000+25000=165000. Sustainable195000; maintenance45000; FCF150000; conversion6000bps. Noncash exception is not added twice; fraud remains null. |
| Rollout impact estimate | Standardized treated17.50→13.00, control15.75→14.75. Difference of improvements3.50minutes. Extend trial, but parallel trends remain missing and confirmed causation is null. |
| Automation routing | P1:120 reviews,1 invalid approval,6 valid rejections. Handling760+errors220=980. P2 exceeds invalid limit; P3 exceeds review capacity; P4 violates regulated approval rule. |
| Work-package assignment | A:J, B:L, C:L, V:K. Labor6000 + three cross-person edges×500=7500. L uses40hours, K10, J20; independent reviewer distinct from A/B. |
| Engagement rescue | B/C/D finishday10,success9000bps, futurecash30000; signed payout35000+9000+8000=52000. Expected net52000×0.9−30000=16800, or1680000cents. Past40000 is sunk. |
| Fault hypothesis constraints | Minimum explanations A/B and B/E; intersection B only. Approved M2 covers B for40. Unknown P5 remains missing; A/E are not confirmed. |
| Lease fencing recovery | Normalized times W1:90,W2:102,W3:103,W4:124,W5:115. Fence8 effective102 rejects W2; W5 exceeds lease. Acks W1/W3/W4, safe contiguous prefix1. Grant9 lacks installed fencing: wait. |
| Consistent restore cut | O2/P2 includes T1–T5, recoverabletime94,RPO6,balance100+30−20+10=120. O2/P3 includes half of T6; O3 is unverified. |
| Regional failover | B/C/F cost60+90+110=260, capacity160. Surviving capacity X120/Y100/Z100/Q160. Shared domain, not region label, drives failure correlation; E is too slow. |
| Expand/contract rollout | Safe sequence E,D,A,B,W,V,F,C; last reversible F. A/B require dual-write. Every prefix must satisfy prerequisites and component/schema invariants. |
| Version contract resolution | A3/W1/C2/H1 cost75, protocol1. A2 path requires W2/H2 and costs100. Cheapest unconstrained bundle fails idempotency and pagination despite matching protocol. |

Reference solvers and independently checked answer keys are included for all 27 tasks.

## Running and interpreting

See the root README for setup and commands. This distribution includes only
these 27 main tasks. Repetitions are fresh attempts on the same questions,
not independent additional tasks. The source packs are fictional; observed
scores do not establish production reliability or domain-wide performance.
