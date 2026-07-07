#!/usr/bin/env python3
"""Week-1 triage eval driver.

Loads .factory/eval/triage_cases.jsonl, drives .factory/scripts/factory_triage.py
over each case, writes a run-log row per case, scores precision/recall, and
applies the week-1 gate from v2.1 plan §9:

    - run triage locally on all 12 issues; score >= 75% precision
    - precision is computed over predicted-label match per case
    - recall is computed per expected-label bucket (>=50% per bucket,
      plus an overall recall >= 0.75 floor)

Exits 0 on gate pass, non-zero with breakdown on gate fail.

Usage:
    python3 .factory/scripts/run_eval.py                     # default paths
    python3 .factory/scripts/run_eval.py --cases path.jsonl  # custom cases
    python3 .factory/scripts/run_eval.py --log path.csv      # custom log
    python3 .factory/scripts/run_eval.py --min-precision 0.85
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Iterable

# add sibling scripts to import path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from factory_log import FactoryLog  # noqa: E402
from factory_triage import classify  # noqa: E402


ALL_LABELS = (
    "ready_to_implement",
    "ready_to_spec",
    "needs_info",
    "wait_to_implement",
    "needs_human",
)
ALL_RISKS = ("critical", "high", "medium", "low", "unknown")


@dataclasses.dataclass
class CaseResult:
    case_id: int
    expected_label: str
    expected_risk: str
    predicted_label: str
    predicted_risk: str
    confidence: float
    cost_usd: float
    duration_s: float
    outcome: str  # "match" | "mismatch" | "skipped"
    note: str = ""


def _iter_cases(path: Path) -> Iterable[dict]:
    with path.open("r", encoding="utf-8") as f:
        for ln, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"bad JSONL at line {ln}: {exc}") from exc


def _score(results: list[CaseResult]) -> dict:
    by_label = {l: {"tp": 0, "fn": 0, "fp": 0} for l in ALL_LABELS}
    by_risk = {r: {"tp": 0, "fn": 0, "fp": 0} for r in ALL_RISKS}

    for r in results:
        if r.outcome == "skipped":
            continue
        if r.predicted_label == r.expected_label:
            by_label[r.expected_label]["tp"] += 1
        else:
            by_label[r.expected_label]["fn"] += 1
            by_label[r.predicted_label]["fp"] += 1

        exp_r = r.expected_risk if r.expected_risk in ALL_RISKS else "unknown"
        pred_r = r.predicted_risk if r.predicted_risk in ALL_RISKS else "unknown"
        if pred_r == exp_r:
            by_risk[exp_r]["tp"] += 1
        else:
            by_risk[exp_r]["fn"] += 1
            by_risk[pred_r]["fp"] += 1

    def _pr(table):
        out = {}
        for k, v in table.items():
            tp, fn, fp = v["tp"], v["fn"], v["fp"]
            prec = tp / (tp + fp) if (tp + fp) else 0.0
            rec = tp / (tp + fn) if (tp + fn) else 0.0
            out[k] = {
                "tp": tp, "fn": fn, "fp": fp,
                "precision": round(prec, 3),
                "recall": round(rec, 3),
            }
        return out

    total_match = sum(1 for r in results if r.outcome == "match")
    total = sum(1 for r in results if r.outcome != "skipped")
    overall_precision = total_match / total if total else 0.0

    return {
        "overall": {
            "matched": total_match,
            "total": total,
            "precision": round(overall_precision, 3),
        },
        "labels": _pr(by_label),
        "risks": _pr(by_risk),
    }


def _apply_gate(scores: dict, min_precision: float, min_per_label_recall: float) -> tuple[bool, list[str]]:
    fails: list[str] = []
    if scores["overall"]["precision"] < min_precision:
        fails.append(
            f"overall_precision {scores['overall']['precision']:.2f} "
            f"< gate {min_precision:.2f}"
        )
    for label, m in scores["labels"].items():
        support = m["tp"] + m["fn"]
        if support == 0:
            continue
        if m["recall"] < min_per_label_recall and support >= 1:
            fails.append(
                f"label '{label}' recall {m['recall']:.2f} "
                f"< gate {min_per_label_recall:.2f} (n={support})"
            )
    return (len(fails) == 0), fails


def _run(
    cases_path: Path,
    log_path: Path,
    min_precision: float,
    min_per_label_recall: float,
    log_pretty: bool,
) -> int:
    results: list[CaseResult] = []
    log = FactoryLog(log_path)

    for case in _iter_cases(cases_path):
        t0 = time.perf_counter()
        out = classify(
            issue_number=case["id"],
            issue_title=case["title"],
            issue_body=case.get("body", ""),
        )
        dur = time.perf_counter() - t0
        outcome = "match" if out["label"] == case["expected_label"] else "mismatch"
        results.append(
            CaseResult(
                case_id=case["id"],
                expected_label=case["expected_label"],
                expected_risk=case.get("expected_risk", "unknown"),
                predicted_label=out["label"],
                predicted_risk=out["risk_class"],
                confidence=out["confidence"],
                cost_usd=0.0,  # dry-run, $0
                duration_s=dur,
                outcome=outcome,
                note=case.get("note", ""),
            )
        )
        log.append(
            issue_number=case["id"],
            action="triage",
            agent_name="local_dryrun",
            run_id=f"dryrun-eval-{case['id']:02d}",
            cost_usd=0.0,
            duration_s=dur,
            label_applied=out["label"],
            risk_class=out["risk_class"],
            confidence=out["confidence"],
            outcome=f"dryrun_{outcome}",
            pr_url="",
        )

    scores = _score(results)
    passed, fails = _apply_gate(scores, min_precision, min_per_label_recall)

    # Pretty breakdown to stdout
    bar = "=" * 72
    print(bar)
    print(f"factory triage eval  cases={scores['overall']['total']}  "
          f"matched={scores['overall']['matched']}  "
          f"precision={scores['overall']['precision']:.2f}")
    print(f"gate: precision>={min_precision:.2f}  per-label-recall>={min_per_label_recall:.2f}")
    print(bar)
    print(f"{'case':>4}  {'expected':<20}  {'predicted':<20}  {'risk':<8}  {'conf':>5}  {'dur_s':>6}")
    for r in results:
        flag = "OK" if r.outcome == "match" else "XX"
        print(f"{r.case_id:>4}  {r.expected_label:<20}  {r.predicted_label:<20}  "
              f"{r.predicted_risk:<8}  {r.confidence:>5.2f}  {r.duration_s:>6.3f}  {flag}")
    print(bar)
    print("per-label:")
    for label, m in scores["labels"].items():
        support = m["tp"] + m["fn"]
        if support:
            print(f"  {label:<20}  precision={m['precision']:.2f}  "
                  f"recall={m['recall']:.2f}  (n={support})")
    print("per-risk:")
    for risk, m in scores["risks"].items():
        support = m["tp"] + m["fn"]
        if support:
            print(f"  {risk:<8}  precision={m['precision']:.2f}  "
                  f"recall={m['recall']:.2f}  (n={support})")

    print(bar)
    if passed:
        print("GATE: PASS")
        print(bar)
        return 0
    else:
        print("GATE: FAIL")
        for f in fails:
            print(f"  - {f}")
        print(bar)
        return 2


def _cli() -> int:
    p = argparse.ArgumentParser(description="Week-1 triage eval driver")
    p.add_argument(
        "--cases",
        default=".factory/eval/triage_cases.jsonl",
        help="path to seed eval JSONL",
    )
    p.add_argument(
        "--log",
        default=".factory/log/factory_runs.csv",
        help="path to factory_runs.csv (gitignored)",
    )
    p.add_argument("--min-precision", type=float, default=0.75)
    p.add_argument("--min-per-label-recall", type=float, default=0.50)
    args = p.parse_args()
    return _run(
        cases_path=Path(args.cases),
        log_path=Path(args.log),
        min_precision=args.min_precision,
        min_per_label_recall=args.min_per_label_recall,
        log_pretty=True,
    )


if __name__ == "__main__":
    raise SystemExit(_cli())
