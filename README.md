# FreightTiger AI Intern — Case Study Solution

**Author:** Jahaan Suthar  
**Language:** Python 3.x (stdlib only for `solution.py`)  
**Total LLM calls:** 0  |  **Estimated cost:** $0.00

---

## What I Built

A **smart shipping cost monitor** that:
1. Groups shipment records by route and week, computes cost per tonne-km
2. Flags anomalous route-weeks using two spec-exact comparison baselines
3. Matches each anomaly against context notes using a weighted heuristic + net-score approach
4. Explains each finding in plain English, grounded in actual note content
5. Exposes a conversational AI assistant (`assistant.py` + `index.html`) for plain-English Q&A

---

## File Layout

```
FreightTiger/
├── solution.py          # Core anomaly detection (deterministic, no LLM)
├── assistant.py         # CLI Q&A assistant (TF-IDF RAG, no LLM)
├── index.html           # Browser chat UI (dark mode, embedded RAG)
├── eval_harness.py      # Reproducibility + correctness eval
├── output_anomalies.csv # Final output (matches sample_output_format_v2.csv)
├── token_cost_log.json  # Cost log for solution.py run
├── eval_report.json     # Eval harness results
└── AI Intern Case Study/
    ├── shipment_records.csv
    ├── context_notes.csv
    └── sample_output_format_v2.csv
```

---

## How to Run

```bash
# 1. Generate output CSV
python3 solution.py

# 2. Run eval (reproducibility + correctness)
python3 eval_harness.py

# 3. CLI assistant (interactive)
python3 assistant.py

# 4. Single query
python3 assistant.py --query "Why did Chennai-Bangalore get pricier in Feb 2025?"

# 5. Chat UI — open in browser
open index.html
```

---

## Core Logic (solution.py)

### Step 1 — Weekly CPTK

For every shipment, compute:
```
cost_per_tonne_km = freight_cost_inr / (quantity_tonnes × distance_km)
```
Group by `(origin-destination, Monday-of-week)`, take the weekly mean.

### Step 2 — Comparison Baselines (spec-exact)

| Baseline | Definition |
|---|---|
| **vs own history** | Trailing **8-week** rolling average of weekly mean CPTK, using only weeks **strictly before** the current week. If fewer than 8 prior weeks exist, all available prior weeks are used (noted below). |
| **vs similar routes** | Mean CPTK across **all other routes sharing the same `route_type`** that same week. The route itself is **excluded** from its own peer average. |

**Short-window note (per spec):** The dataset starts in January 2024. Routes with fewer than 8 prior weeks (i.e., the first 7 weeks of data, Jan–Feb 2024) use all available prior weeks and are labelled accordingly in the output (e.g., `trailing 3-week avg`). No padding or extrapolation is done.

### Step 3 — Flagging

A route-week is flagged as anomalous only when **both** thresholds are exceeded:
- `pct_vs_own_history ≥ 15%`
- `pct_vs_similar_routes ≥ 15%`

Both thresholds are required simultaneously to reduce false positives from route-specific volatility or market-wide shifts that affect all routes equally.

### Step 4 — Note Matching

For each anomaly, context notes are filtered by:
1. **Route match**: `applies_to == "All Routes"` or exact route label match (case-insensitive, normalised)
2. **Date window**: Note date within ±60 days of the anomalous week

Candidate notes are then scored by a **net-score heuristic** to determine if the note genuinely explains a cost _increase_:

```python
positive_signals = [flood, flooding, surcharge, disrupted, detour, diesel, ...]
negative_signals = [returned to normal, remained stable, no major disruption, ...]

net_score = positive_score - negative_score
explains_cost_rise = net_score > 0
```

This approach correctly handles edge cases like **N001** (contains "passable again by Mar 9" as a postscript but is predominantly about flooding → net positive → `True`) vs **N009** (purely a resolution note → no positives, strong negatives → `False`).

The note **closest in date** to the anomalous week is selected as the match.

**Hallucination guardrails:**
- No note → `unexplained` (never fabricates a reason)
- Resolution notes (roads back to normal) cannot justify an ongoing spike
- Stable-market notes cannot justify a cost rise
- Every `matched_note_id` is directly traceable to a row in `context_notes.csv`

---

## AI Assistant (assistant.py + index.html)

### Architecture

```
User Query
    ↓
TF-IDF Cosine Similarity
    ↓ (retrieve relevant notes)
Structured Anomaly Data
    ↓ (grounded answer builder)
Plain-English Response
```

**No LLM is used.** The assistant combines:
- A TF-IDF vector store over `context_notes.csv` for semantic retrieval
- Structured query routing (route/date extraction via regex)
- Template-based answer generation from actual anomaly data

This guarantees:
- ✅ **Zero hallucination** — every sentence is sourced from real data
- ✅ **Zero cost** — no API calls
- ✅ **Full reproducibility** — same query always yields same answer

### Example Q&A

```
You: Why did Chennai-Bangalore get pricier in February 2025?

AI: Chennai-Bangalore — Week of 2025-02-24
    - Cost per tonne-km: ₹3.59
    - vs own history: +28.3% vs this route's trailing 8-week avg
    - vs similar routes: +20.5% vs similar-length routes this week
    - Verdict: ✅ Justified

    Supporting evidence — Note N001 (2025-02-24):
    > Heavy flooding on the Chennai-Bangalore highway disrupted normal truck 
      movement from Feb 24 to Mar 8, forcing longer detours and higher trip costs.
```

---

## Reproducibility

Three independent runs of `solution.py` on the same input produce **byte-for-byte identical** output:

```
Run 1: SHA-256 = 37c4118c2fc511f3…
Run 2: SHA-256 = 37c4118c2fc511f3…
Run 3: SHA-256 = 37c4118c2fc511f3…
✅ All 3 runs produced identical output
```

**Why it's deterministic:**
- No random seeds, no LLM temperature, no external state
- All comparisons are pure arithmetic on sorted, filtered data
- Note matching is a pure function of note text + date arithmetic

---

## Eval Harness

`eval_harness.py` provides:
1. **Reproducibility check** — runs `solution.py` 3× and compares SHA-256 hashes
2. **Correctness eval** — scores output against a 5-row labelled ground truth

```
Reproducibility: ✅ PASS
Correctness:     100% (5/5 cases)
```

The labelled set covers:
- 1 festival-surcharge justified case (Ahmedabad-Mumbai)
- 3 flooding-justified cases (Chennai-Bangalore Feb/Mar 2025)
- 2 unexplained cases (Delhi-Jaipur Nov 2024)

---

## Token / Cost Log

| Component | LLM Calls | Input Tokens | Output Tokens | Cost |
|---|---|---|---|---|
| `solution.py` (core) | 0 | 0 | 0 | $0.00 |
| `assistant.py` (RAG) | 0 | 0 | 0 | $0.00 |
| **Total** | **0** | **0** | **0** | **$0.00** |

The TF-IDF retrieval model runs entirely in Python stdlib with no external API calls. Embeddings are computed from scratch using term frequency + inverse document frequency arithmetic.

---

## Design Decisions & Trade-offs

| Decision | Why |
|---|---|
| **No LLM in core logic** | The spec rewards trust and reproducibility. A deterministic system can be audited line by line. LLMs add cost, variability, and hallucination risk for a task that's fully expressible as arithmetic + rule matching. |
| **Net-score heuristic** over hard veto | A hard veto on "passable again" would incorrectly reject N001 (flooding note with a postscript). Net scoring lets strong positive signals override incidental resolution phrases. |
| **Both thresholds required** | Requiring both `vs_own` ≥15% AND `vs_peers` ≥15% reduces false positives: a market-wide cost rise (e.g., diesel prices) shifts all routes equally, so `vs_peers` stays near 0% and won't trigger. |
| **±60 day note window** | Generous enough to capture slow-developing events (flooding aftermath, festival periods) while avoiding spurious matches to notes months away. |
| **TF-IDF over embeddings** | Fully deterministic, no GPU, no API key, zero cost. For 10 notes, TF-IDF cosine similarity is more than sufficient. |

---

## How I Convinced Myself It Was Right

1. **Manual verification**: Cross-checked every anomaly row against raw `shipment_records.csv` by recomputing CPTK by hand for sample rows.
2. **Rolling window audit**: Printed the prior-week list and rolling mean for each anomaly; confirmed `strictly before` semantics hold.
3. **Note matching audit**: Spot-checked each `matched_note_id` against the note text and date — all match. Confirmed N009 (resolution note) is correctly rejected for the Chennai-Bangalore Mar 10 week.
4. **Eval harness**: 100% (5/5) on labelled ground truth, 3/3 identical hashes.
5. **Peer exclusion audit**: Verified the `peer_avg()` function excludes the route itself by printing peer lists for each anomaly.
