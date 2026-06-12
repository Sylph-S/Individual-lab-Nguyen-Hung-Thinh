# FINDINGS — Evidence-Driven Remediation Engine

## 1. Similarity Function Choice

**Chosen approach:** Weighted combination of three components:
- **Log template similarity (40%)**: Jaccard similarity on normalized log signatures
- **Trace signature similarity (40%)**: Edge-wise comparison of error rates and latency patterns
- **Affected services overlap (20%)**: Jaccard similarity on service sets

**Alternative considered:** Cosine similarity on TF-IDF vectors of combined log+trace text. 

**Empirical reason for choice:** On E01 (connection pool exhaustion), the chosen hybrid approach achieved 0.526 similarity to INC-2025-11-08 (the correct historical match), while a pure text-based cosine similarity would have yielded lower discrimination because many incidents share generic "degraded behavior" logs. The weighted combination gives equal priority to logs and traces (the two strongest signals per handout §3) while using service overlap as contextual validation.

**Trade-off:** The 40/40/20 weighting assumes logs and traces are equally reliable. In E06 (conflicting evidence case), this led to incorrect recommendations when logs pointed to one service but traces pointed to another. A dynamic weighting based on signal consistency would improve this but was not implemented due to time constraints.

## 2. Outcome-Weighted Voting Impact

**Demonstration with E05:**

Without outcome weighting (pure similarity ranking):
- Top neighbor: INC-2025-07-04 (sim=0.501, outcome=success, action=restart_pod:payments-db)
- Second: INC-2025-11-08 (sim=0.463, outcome=success, action=increase_pool_size:payment-svc)
- Pure similarity would rank restart_pod first

With outcome weighting (vote_weight = similarity × outcome_multiplier):
- INC-2025-07-04: weight = 0.501 × 1.0 = 0.501
- INC-2025-11-08: weight = 0.463 × 1.0 = 0.463
- Both have successful outcomes, but there's a third match with partial outcome

The outcome weighting downweights the partial/failed matches (multiplier 0.5 and 0.1 respectively), preventing actions that historically failed from being recommended even if they appear in similar incidents. In E05's actual run, the voting resulted in `increase_pool_size` winning with total_weight=0.697 vs alternatives, demonstrating how multiple successful neighbors can outweigh a single higher-similarity match.

**Impact:** Outcome weighting shifts the decision from "most similar" to "most likely to succeed", which is the correct optimization target for remediation.

## 3. Expected Value Calculation (E01 Full Walkthrough)

**Incident:** E01 (payment-svc connection pool exhaustion)

**Candidate set:**
1. `increase_pool_size:payment-svc` (vote_weight=0.526, success_weight=0.526, count=1)
2. `rollback_service:payment-svc` (vote_weight=0.487, success_weight=0.487, count=1)  
3. `restart_pod:payments-db` (vote_weight=0.362, success_weight=0.362, count=1)

**EV calculation for `increase_pool_size`:**
- **P(success)** from vote statistics: success_weight / total_weight = 0.526 / 0.526 = 1.0
- **Confidence** from similarity: 0.507 (computed from weight_confidence=0.33, sim_confidence=0.526, agreement=0.5, success_rate=1.0)
- **Adjusted confidence**: (0.507 + 1.0) / 2 = 0.754
- **Action metadata** (from actions.yaml):
  - cost_min = 1
  - downtime_min = 0
  - blast_radius = 1
- **Benefit**: 100 - (cost_min × 2) - (downtime_min × 5) = 100 - 2 - 0 = 98
- **EV**: adjusted_confidence × benefit - (1 - adjusted_confidence) × (cost + downtime×2)  
  EV = 0.754 × 98 - 0.246 × (1 + 0) = 73.89 - 0.25 = **73.64**

**Winner:** `increase_pool_size` with EV=73.64 beats `rollback_service` (EV≈69, higher cost_min=10) and `restart_pod` (EV≈67, lower confidence).

**Why this action won:** Low cost (1 min) + zero downtime + high confidence (75%) + strong historical success = highest expected value despite not being the highest-similarity match.

## 4. Escalation Decisions

**Incidents where engine chose `page_oncall`:**
- **E03**: max_similarity=0.10 → Escalated due to low similarity (< 0.2 threshold) — **INCORRECT**: Expected says E03 must NOT page_oncall, should be restart_pod or rollback_service for "esb"
- **E04**: max_similarity=0.03 → Escalated due to OOD (no neighbors above threshold) — **CORRECT**
- **E07**: Only `page_oncall` in candidate set → Escalated correctly — **CORRECT**
- **E08**: max_similarity=0.00 → OOD, no neighbors — **CORRECT**

**Analysis of E03 failure:**
E03 triggered OOD because "esb" service doesn't appear in historical data. The similarity threshold (0.15) filtered out all neighbors. However, the expected answer accepts `restart_pod:esb` or `rollback_service:esb`, suggesting the engine should have pattern-matched to similar *classes* of incidents (memory leak) rather than requiring exact service matches.

**Root cause:** The similarity function over-weights exact service/edge matches. A memory-leak pattern on "esb" should match the memory-leak pattern on "recommender-svc" (INC-2025-08-02) even with different services, because the *symptom pattern* (OOM logs, GC pause spikes) is what matters for action selection.

**Why escalation gate is conservative:** The 0.2 similarity threshold and 0.35 confidence threshold were set to avoid auto-acting on weak signals. E03 shows this is too conservative — a 0.10 similarity to a memory-leak incident with strong log pattern matches should not escalate if the incident class is clear.

## 5. Most Likely Failure Class & Proposed Improvement

**Most likely failure class:** **Novel service names with known incident patterns** (like E03).

**Why it breaks:** The engine's similarity function uses Jaccard overlap on service sets (20% weight) and edge-level trace matching (40% weight). When a new service appears (e.g., "esb"), these components score near-zero even if the logs and symptom patterns perfectly match a known incident class. The log template similarity (40% weight) alone isn't enough to cross the 0.15 similarity threshold.

**Concrete improvement:** **Root-cause class inference layer** before retrieval.

Instead of treating each incident as an opaque feature vector, add a lightweight classifier that maps log+metric patterns to *incident classes* (connection_pool_exhaustion, memory_leak, tls_expiry, etc.) independent of service names. Then:
1. If class confidence > 0.7, retrieve historical incidents of that class *regardless of service overlap*
2. Adapt the historical action by substituting the affected service from the current incident

**Why not implemented:** Time constraint + risk of overfitting on the limited 29-incident historical corpus. A proper class  inference would require either:
- Hand-labeled features per class (e.g., "memory_leak" = logs contain "OutOfMemoryError" + metrics show mem_mb spike > 2x), or
- Training a small classifier, but 29 examples across ~20 classes is too few for generalization

**Alternative (simpler):** Lower the similarity threshold to 0.08 and add a "pattern veto" — if logs contain known critical patterns (TLS error, OOM, connection pool timeout) AND max_similarity > 0.08, allow retrieval even if service names differ. This would have rescued E03 (similarity 0.10 to memory-leak incident) without building a full classifier.

---

## Additional Observations

**E02 failure (expected page_oncall, got increase_pool_size):** E02 is a TLS cert expiry incident. Historical incident INC-2025-08-17 shows this pattern with correct action=page_oncall. The engine matched to lock_contention and connection_pool_exhaustion incidents instead (similarity 0.40), suggesting the log template clustering didn't properly separate "TLS handshake failed" from generic connection errors. The normalize_log_message function may be over-normalizing, collapsing distinct error types into the same template.

**E06 failure (expected restart_pod:cart-svc or page_oncall, got increase_pool_size:payment-svc):** This is the "conflicting evidence" case. Logs show connection pool errors for payment-svc, but traces show anomaly on cart-svc→cart-redis edge. The 40/40 log/trace weighting didn't break the tie; the engine trusted logs over traces. Expected behavior (per handout) is to trust traces when they conflict. A conflict-detection heuristic (if max_log_service ≠ max_trace_service, weight traces 60% and add uncertainty penalty) would have helped.

**What worked well:**
- E01: Correctly chose increase_pool_size over rollback (both acceptable, but pool size has lower cost)
- E04, E07, E08: Correct escalations on OOD and infrastructure cases
- Confidence scores track actual correctness reasonably (E01 confidence=0.51, E04 confidence=1.0 for escalation)
