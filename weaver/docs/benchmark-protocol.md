# HeroBench Benchmark Protocol — Experiment Design for IEEE Paper

**Status:** Draft
**Date:** 2026-04-15
**Author:** Todd / Claude Opus 4.6
**Companion spec:** `weaver-herobench.md` (architecture + infrastructure)
**Target venue:** IEEE paper on local agent infrastructure

Parent charter: `weaver-demo-PRD` (this is the herobench/ sub-area of the weaver-demo consumer crate).

---

## 1. Thesis Claims Under Test

The HeroBench integration (specified in `weaver-herobench.md`) is infrastructure
for testing architectural claims. This document defines the experiment design:
which conditions to run, what to measure, and what constitutes evidence.

Three thesis claims:

1. **Graph memory enables transfer.** The agent reuses weighted subgraphs
   from easier tasks when planning harder ones — performance at difficulty N
   benefits from experience at difficulty N-1.

2. **Harness-driven failure recording works.** The harness updates edge
   weights and records failure modes without relying on voluntary model
   behavior. This eliminates the write asymmetry observed in the Hanoi
   benchmark (models write memory on success ~100% but on failure ~3%).

3. **Belief graphs are executable.** The task dependency graph the agent
   builds is structurally identical to the strategy graphs from Research #88
   — validating the graph-in-graph model before StrategyEngine exists.

### Why HeroBench Before Minecraft

| Variable | HeroBench | Minecraft |
|----------|-----------|-----------|
| Timing | Turn-based — server waits | Real-time — 50ms tick budget |
| Protocol | HTTP/JSON (reqwest) | Custom packets (azalea, nightly) |
| Difficulty | 9 pre-built levels, 844 tasks | Self-selected, unbounded |
| Evaluation | Built-in scoring pipeline | Custom metrics |
| Setup | `pip install` + FastAPI | Paper MC + JVM + azalea |

If the architecture works in HeroBench but degrades in Minecraft, the
degradation isolates **latency** as the variable — directly proving the
"latency is the enemy of agency" thesis. If it fails in HeroBench, the
problem is the architecture itself, and we've saved months of Minecraft
integration work.

---

## 2. Experimental Conditions

The benchmark infrastructure supports independently toggleable features via
CLI flags (see `weaver-herobench.md` §8.3 for the flag table and CLI design).
Conditions are composed by combining flags.

### Core Conditions (minimum viable experiment)

| Condition | Flags | Tools available | What it tests |
|-----------|-------|----------------|---------------|
| **A: Baseline** | `--no-belief-graph --no-plan-tool` | Observe (raw) + Act | Raw model capability without memory |
| **B: Harness memory** | `--with-belief-graph --with-memory-augmentation --no-plan-tool` | Observe (augmented) + Act | Automatic memory transfer without voluntary model behavior |
| **C: Full system** | `--with-belief-graph --with-memory-augmentation --with-plan-tool` | Observe (augmented) + Act + Plan | Planning + graph reuse on top of memory |

### Isolation Analysis

**A vs B** isolates harness-driven memory. If B beats A at higher difficulty
levels, automatic memory augmentation improves performance without requiring
the model to voluntarily call memory tools. This is the core thesis claim —
the harness takes responsibility for recording and surfacing experience.

**B vs C** isolates planning. If C beats B, explicit task decomposition with
subgraph reuse adds value beyond automatic memory injection. This tests
whether models benefit from structured graph-based planning.

### Extended Conditions (as needed for specific claims)

| Condition | Flags | What it tests |
|-----------|-------|---------------|
| **D: Planning without memory** | `--no-belief-graph --with-plan-tool` | Does planning alone help, or only when backed by experience data? |
| **E: Voluntary memory** | `--no-belief-graph --with-voluntary-memory` | Does the Hanoi finding replicate? (models don't voluntarily use memory tools on failure) |
| **F: Risk profiles** | B + `--with-risk-profiles` | Do domain-specific priors (optimistic crafting, pessimistic combat) improve decision quality? |
| **G: Ambient risk** | F + `--with-ambient-risk` | Does environmental risk modulation (HP, proximity, streak) change agent behavior? |

Not every condition needs to be run. The minimum viable experiment is **A vs B**.
Additional conditions are added if the paper's argument requires them.

---

## 3. Study Design

### Task Selection

For each difficulty level, randomly select N tasks from the 20 available per
level. **Use the same task set across all conditions** to ensure comparability.
Record the random seed for reproducibility.

Recommended: 5 tasks per level, difficulty range 1-9. This gives 45 data
points per condition — sufficient for per-difficulty success rate curves.

### Character Reset Policy

Characters reset per task across ALL conditions. The belief graph persists
across tasks (in conditions where it's enabled). This isolates graph memory
as the transfer variable — the agent starts each task with no carryover
items/XP but retains all learned knowledge in HADES.

### Ordering

Run tasks in ascending difficulty order within each condition. The belief
graph (Conditions B, C) accumulates across tasks, so easier tasks seed the
graph with experience that harder tasks can retrieve. This models a real
deployment where an agent gets progressively harder work.

### Replication

Run each condition at least twice with different random task selections.
If results diverge significantly, run a third time. The belief graph starts
fresh for each condition run (wipe `belief_nodes`, `belief_edges`, etc.).

---

## 4. Metrics and Evidence

### Primary Evidence: Success Rate vs Difficulty

```
Success Rate (%)
100 +  *-----*-----*
    |  o-----o---o
 80 +             \  *-----*
    |               o       \
 60 +                        *-----*
    |                  o       \
 40 +                          o    *
    |                               \
 20 +                            o    o
    |
  0 +-----+-----+-----+-----+-----+-----+-----+-----+-----+
    1     2     3     4     5     6     7     8     9
                        Difficulty Level

* = With belief graph (Condition B or C)
o = Without belief graph (Condition A)
```

The gap between curves IS the thesis. At low difficulty, both succeed
(the task is simple enough to solve without memory). At high difficulty,
the belief graph enables transfer — reusing subgraphs, avoiding known
failure modes, planning with confidence estimates.

**What we expect:** Convergence at difficulty 1-2 (both succeed), divergence
starting at difficulty 3-4, widening gap through difficulty 7-9. If the
curves don't diverge, either the architecture doesn't help or the model
is too weak for the task regardless.

### Secondary Evidence: Belief Graph Growth

```
Nodes + Edges
800 +                                          /
    |                                        /
600 +                                      /
    |                                   /
400 +                              /--/
    |                          /--/
200 +                    /--/
    |              /--/
100 +        /--/
    |  /--/
  0 +--/--+-----+-----+-----+-----+-----+-----+-----+-----+
    1     2     3     4     5     6     7     8     9
                    Tasks Completed (cumulative)
```

Growth should be sublinear (later tasks reuse earlier nodes rather than
creating new ones). The reuse rate at difficulty 5+ is the transfer metric.

### Evidence Checklist

| Metric | Source | Condition(s) | Thesis claim |
|--------|--------|-------------|-------------|
| Success rate by difficulty | `DifficultyResult.success_rate` | A, B, C | Memory enables transfer |
| Belief graph growth curve | `BeliefGraphStats.total_nodes/edges` per difficulty | B, C | Graph grows sublinearly (reuse) |
| Plan confidence vs actual success | `TaskAttemptResult.plan_confidence` vs `solved` | C | Belief graph calibrates well |
| Node reuse rate by difficulty | `TaskAttemptResult.plan_steps_reused` | C | Higher levels reuse lower-level subgraphs |
| Constraint-prevented failures | Count of constraints surfaced in observation | B, C | Learned constraints prevent repeat failures |
| Voluntary memory use rate | HadesMemoryRead/Write call count | E | Replication of Hanoi finding |
| Latency breakdown | `EpisodeLatency` | All | Predicts Minecraft feasibility |

### Specific Claims per Condition Comparison

**A vs B (core thesis):**
- B should show higher success rates at difficulty 4+
- B should show the agent avoiding known failure modes (constraint count > 0)
- B's latency overhead (HADES reads) should be < 10ms (negligible vs inference)

**B vs C (planning value-add):**
- C should show higher plan confidence correlating with actual success
- C should show more node reuse (structured plans find existing subgraphs)
- C's additional overhead (plan validation + HADES writes) should be bounded

**Condition E (Hanoi replication):**
- Expect voluntary memory write rate on failure < 10% (matching Hanoi's ~3%)
- Expect voluntary memory write rate on success > 80%
- This validates that harness-driven recording (Condition B) is necessary

---

## 5. Phase-Specific Experiment Goals

### Phase 2B (Risk Profiles) — if needed

Run Condition B with and without `--with-risk-profiles`. Compare:
- Combat task success rate (expect improvement with pessimistic combat priors)
- Crafting task success rate (expect no change or slight improvement)
- Agent deaths per episode (expect fewer with risk-aware weights)

**Thesis claim:** Domain-aware risk tolerance improves decision quality in
domains with asymmetric failure costs.

### Phase 2C (Ambient Risk) — if needed

Run Condition F (which includes risk profiles) with and without
`--with-ambient-risk`. Compare:
- Agent behavior when HP is low (expect more conservative actions)
- Plan abandonment rate (expect earlier abort when ambient risk is high)
- Survival rate in multi-step combat sequences

**Thesis claim:** Environmental context should modulate risk tolerance in
real-time. This validates the proto-interrupt signal before building the
full interrupt system.

---

## 6. Example Invocations

### Minimum Viable Experiment (A vs B)

The canonical entry point is `weaver agent start tasks/herobench-*.yaml`. Each
condition is a task work spec (`tasks/herobench-<condition>.yaml`) that pins the
server, character, difficulty range, tasks-per-level, max-attempts, output path,
and the condition's feature toggles (belief graph, memory augmentation, plan
tool, voluntary memory).

```bash
# Condition A: Baseline (no belief graph, no planning)
weaver agent start tasks/herobench-condition-A.yaml

# Condition B: Harness memory (belief graph + augmented observation)
weaver agent start tasks/herobench-condition-B.yaml
```

### Full Condition Sweep

```bash
# Condition C: Full system
weaver agent start tasks/herobench-condition-C.yaml

# Condition E: Voluntary memory (Hanoi replication)
weaver agent start tasks/herobench-condition-E.yaml
```

---

## 7. Analysis Workflow

1. Load JSON result files for each condition
2. Per-difficulty success rate comparison (primary graph)
3. Belief graph growth curve (secondary graph — Conditions B, C only)
4. Latency breakdown histograms (all conditions)
5. Plan confidence calibration plot (Condition C: predicted vs actual)
6. Statistical comparison: paired t-test or Wilcoxon signed-rank on
   matched task pairs across conditions
7. Export figures for IEEE paper

### Data Format

All results are in the `HeroBenchResult` JSON schema defined in
`weaver-herobench.md` §8.2. Key fields for analysis:

- `difficulty_levels[].success_rate` — primary metric
- `difficulty_levels[].tasks[].attempts[].plan_confidence` — calibration
- `belief_graph_stats` — growth/reuse metrics
- `difficulty_levels[].mean_wall_clock_ms` — latency
