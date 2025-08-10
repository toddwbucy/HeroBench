import json
import os
import numpy as np

PLOTS_DIR = "plots1"  # still needed for paths
os.makedirs(PLOTS_DIR, exist_ok=True)

# ─── MODEL SPECIFICATIONS ─────────────────────────────────────────────────────
MODEL_SPECS = {
    "qwen3_8b":                     {"path": "results_final/qwen3_8b.json",                    "think": False},
    "qwen3_8b_think":                     {"path": "results_final/qwen3_8b_think.json",              "think": True},   # same display; diff style
    "qwen3_32b":                    {"path": "results_final/qwen3_32b.json",                   "think": False},
    "qwen3_32b_think":                    {"path": "results_final/qwen3_32b_think.json",             "think": True},
    "qwen3-235b-a22":               {"path": "results_final/qwen3-235b-a22.json",              "think": True},
    "qwen3-235b-a22-2507":               {"path": "results_final/qwen3-235b-a22b-thinking-2507.json",              "think": True},
    
    "deepseek-v3":                  {"path": "results_final/deepseek-v3.json",                      "think": False},
    "deepseek-r1-0528":             {"path": "results_final/deepseek-r1-0528.json",            "think": True},
    "DeepSeek-R1-70B":              {"path": "results_final/DeepSeek-R1-Distill-Llama-70B.json","think": True},
    "gemini-2.5-pro":               {"path": "results_final/gemini-2.5-pro.json",            "think": True},
    "gemini-2.5-flash":             {"path": "results_final/gemini-2.5-flash.json",            "think": True},
    "claude-sonnet-4":              {"path": "results_final/claude-sonnet-4.json",       "think": False},
    "claude-sonnet-4_think":              {"path": "results_final/claude-sonnet-4_think.json",       "think": True},
    "gpt-4.1-mini":                 {"path": "results_final/gpt4.1_mini.json",                 "think": False},
    "gpt-4.1":                      {"path": "results_final/gpt-4.1.json",                     "think": False},
    "gpt-oss-120b2":                      {"path": "results_final/gpt-oss-120b2.json",                "think": True},
    "gpt-oss-120b1":                      {"path": "results_final/gpt-oss-120b1.json",                "think": True},
    "o4-mini":                      {"path": "results_final/o4-mini_high.json",                "think": True},
    "o3":                           {"path": "results_final/o3_high.json",                     "think": True},
    "magistral-medium-2506":        {"path": "results_final/magistral-medium-2506:thinking.json",   "think": True},
    "grok-4":                       {"path": "results_final/grok-4.json",                      "think": True},
    "kimi-k2":                      {"path": "results_final/kimi-k2.json",                          "think": False},
}
# ─── HELPERS ─────────────────────────────────────────────────────────────────
def analyze_results(json_path):
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    diffs, succs, scores, toks = [], [], [], []
    for diff_str, tasks in sorted(data.items(), key=lambda x: float(x[0])):
        all_r, all_sc, all_t = [], [], []
        for td in tasks.values():
            all_r.extend(td.get("results", []))
            all_sc.extend(td.get("scores", []))
            all_t.extend(td.get("tokens", []))
        n = len(all_r)
        wins = sum(1 for r in all_r if r=="win")
        succ = wins/n if n else 0
        avg_sc = (sum(all_sc)/len(all_sc)) if all_sc else 0
        valid_t = [t for t in all_t if t is not None]
        avg_t = (sum(valid_t)/len(valid_t)) if valid_t else 0

        diffs .append(float(diff_str))
        succs .append(succ)
        scores.append(avg_sc)
        toks  .append(avg_t)

    return diffs, succs, scores, toks

# ─── LOAD & COMPUTE ───────────────────────────────────────────────────────────
all_metrics = {}
for name, spec in MODEL_SPECS.items():
    diffs, succ, sc, tks = analyze_results(spec["path"])
    all_metrics[name] = {
        "difficulty":    diffs,
        "success_rate":  succ,
        "avg_score":     sc,
        "avg_tokens":    tks,
    }

model_names = list(all_metrics.keys())

# ─── PRINT DETAILED PER-MODEL TABLES ─────────────────────────────────────────
for name in model_names:
    m = all_metrics[name]
    print(f"\nModel: {name}")
    print(f"{'Difficulty':>10}  {'Success':>8}  {'Score':>7}  {'Tokens':>7}")
    print("-" * 40)
    for d, s, sc, t in zip(m["difficulty"], m["success_rate"], m["avg_score"], m["avg_tokens"]):
        print(f"{d:10.2f}  {s:8.2f}  {sc:7.2f}  {t:7.2f}")
print()

# ─── SUMMARY STATISTICS ───────────────────────────────────────────────────────
mean_success = [np.mean(all_metrics[n]["success_rate"]) for n in model_names]
mean_score   = [np.mean(all_metrics[n]["avg_score"])     for n in model_names]
mean_tokens  = [np.mean(all_metrics[n]["avg_tokens"])    for n in model_names]

norm_success = [s/t if t else 0 for s, t in zip(mean_success, mean_tokens)]
norm_score   = [sc/t if t else 0 for sc, t in zip(mean_score,   mean_tokens)]

SCALING = 10000   # Use 1000 or 10000 as you like

scaled_norm_success = [x * SCALING for x in norm_success]
scaled_norm_score = [x * SCALING for x in norm_score]

print(f"Summary across difficulties (per {SCALING:,} tokens):")
print(f"{'Model':30}  {'Mean Succ':>9}  {'Mean Sc':>8}  {'Mean Tok':>9}  {'Succ/{0}Tok'.format(SCALING//1000):>11}  {'Sc/{0}Tok'.format(SCALING//1000):>9}")
print("-" * 90)
for name, ms, msc, mt, ns, nts in zip(model_names, mean_success, mean_score, mean_tokens, scaled_norm_success, scaled_norm_score):
    print(f"{name:30}  {ms:9.2f}  {msc:8.2f}  {mt:9.2f}  {ns:11.2f}  {nts:9.2f}")
