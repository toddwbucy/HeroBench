import json
import os
import numpy as np

PLOTS_DIR = "plots1"  # still needed for paths
os.makedirs(PLOTS_DIR, exist_ok=True)

# ─── MODEL SPECIFICATIONS ─────────────────────────────────────────────────────
MODEL_SPECS = {
    "gemini-2.5-pro (think)":       {"path": "results1/gemini-2.5-pro_full_3.json",             "think": True},
    "claude-sonnet-4 (think)":      {"path": "results_final/claude-sonnet-4_think.json",       "think": True},
    "claude-sonnet-4 (think) level":      {"path": "results_level/claude-sonnet-4_level.json",       "think": True},
    "claude-sonnet-4 (think) level noise":      {"path": "results_level/claude-sonnet-4_level_noise.json",       "think": True},

    "o3 (think)":                   {"path": "results_final/o3_high.json",                     "think": True},
    "o3 (think) level":                   {"path": "results_level/o3_level.json",                     "think": True},
    "o3 (think) level noise":                   {"path": "results_level/o3_noise_level.json",                     "think": True},
    "grok-4 (think)":               {"path": "results_final/grok-4.json",                      "think": True},
    "grok-4 (think) level":               {"path": "results_level/grok-4_level.json",                      "think": True},
    "grok-4 (think) level noise":               {"path": "results_level/grok-4_noise_level_2.json",                      "think": True},
    "gemini-2.5-pro (think) level":               {"path": "results_level/gemini-2.5-pro.json",                      "think": True},
    "gemini-2.5-pro (think) level noise":               {"path": "results_level/gemini_2.5-pro_noise_level2.json",                      "think": True},



}

def compute_for_difficulty(json_path, target_diff=9.0):
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    bucket = data.get(str(int(target_diff)), {})
    all_results = []
    all_scores  = []
    all_tokens  = []
    for task in bucket.values():
        all_results.extend(task.get("results", []))
        all_scores.extend(task.get("scores", []))
        all_tokens.extend(task.get("tokens", []))
    n = len(all_results)
    wins = sum(1 for r in all_results if r == "win")
    success = wins / n if n else 0.0
    avg_score = sum(all_scores) / len(all_scores) if all_scores else 0.0
    valid_tokens = [t for t in all_tokens if t is not None]
    avg_tokens = sum(valid_tokens) / len(valid_tokens) if valid_tokens else 0.0
    return success, avg_score, avg_tokens

print(f"{'Model':30s}  {'Succ@9':>7s}  {'Score@9':>8s}  {'Tok@9':>7s}")
print("-" * 58)
for name, spec in MODEL_SPECS.items():
    succ, sc, tk = compute_for_difficulty(spec["path"], target_diff=9.0)
    print(f"{name:30s}  {succ:7.2f}  {sc:8.2f}  {tk:7.1f}")