#!/usr/bin/env python3
"""Append-only factory run logger.

Schema mirrors v2.1 plan §8 (Storage + factory_runs columns):
    ts_iso, issue_number, action, agent_name, run_id,
    cost_usd, duration_s, label_applied, risk_class,
    confidence, outcome, pr_url

Default path: .factory/log/factory_runs.csv (gitignored).
Weekly exports: docs/factory/weekly-{ISO_YEAR}-W{ISO_WEEK}.csv
                (committed; greppable for ops).

Library use:
    from factory_log import FactoryLog
    log = FactoryLog("/path/to/.factory/log/factory_runs.csv")
    log.append(
        issue_number=42, action="triage", agent_name="local_dryrun",
        run_id="dryrun-001", cost_usd=0.0, duration_s=0.12,
        label_applied="ready_to_implement", risk_class="medium",
        confidence=0.81, outcome="success", pr_url="",
    )

CLI use (used by run_eval.py and GHA workflows):
    python3 .factory/scripts/factory_log.py append \\
        --issue-number 42 --action triage --agent-name local_dryrun \\
        --run-id dryrun-001 --cost-usd 0.00 --duration-s 0.12 \\
        --label-applied ready_to_implement --risk-class medium \\
        --confidence 0.81 --outcome success
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
from pathlib import Path

COLUMNS = (
    "ts_iso",
    "issue_number",
    "action",
    "agent_name",
    "run_id",
    "cost_usd",
    "duration_s",
    "label_applied",
    "risk_class",
    "confidence",
    "outcome",
    "pr_url",
)


class FactoryLog:
    def __init__(self, csv_path: str | os.PathLike[str]) -> None:
        self.path = Path(csv_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            with self.path.open("w", newline="") as f:
                csv.writer(f).writerow(COLUMNS)

    def append(
        self,
        *,
        issue_number: int,
        action: str,
        agent_name: str,
        run_id: str,
        cost_usd: float,
        duration_s: float,
        label_applied: str,
        risk_class: str,
        confidence: float,
        outcome: str,
        pr_url: str = "",
        ts_iso: str | None = None,
    ) -> None:
        ts = ts_iso or dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        row = {
            "ts_iso": ts,
            "issue_number": issue_number,
            "action": action,
            "agent_name": agent_name,
            "run_id": run_id,
            "cost_usd": f"{float(cost_usd):.4f}",
            "duration_s": f"{float(duration_s):.3f}",
            "label_applied": label_applied,
            "risk_class": risk_class,
            "confidence": f"{float(confidence):.3f}",
            "outcome": outcome,
            "pr_url": pr_url or "",
        }
        with self.path.open("a", newline="") as f:
            csv.DictWriter(f, fieldnames=COLUMNS).writerow(row)

    def rows(self) -> list[dict[str, str]]:
        if not self.path.exists():
            return []
        with self.path.open("r", newline="") as f:
            return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _cli() -> int:
    p = argparse.ArgumentParser(description="Factory run-log writer")
    p.add_argument("subcommand", choices=("append", "tail"))
    p.add_argument("--csv-path", default=".factory/log/factory_runs.csv")
    # append-mode args
    p.add_argument("--issue-number", type=int)
    p.add_argument("--action", choices=("triage", "implement", "verify"))
    p.add_argument("--agent-name")
    p.add_argument("--run-id")
    p.add_argument("--cost-usd", type=float, default=0.0)
    p.add_argument("--duration-s", type=float, default=0.0)
    p.add_argument("--label-applied")
    p.add_argument("--risk-class")
    p.add_argument("--confidence", type=float, default=0.0)
    p.add_argument("--outcome", default="success")
    p.add_argument("--pr-url", default="")
    args = p.parse_args()

    if args.subcommand == "append":
        for required in (
            "issue_number", "action", "agent_name", "run_id",
            "label_applied", "risk_class",
        ):
            if getattr(args, required) in (None, ""):
                raise SystemExit(f"--{required.replace('_','-')} is required")
        FactoryLog(args.csv_path).append(
            issue_number=args.issue_number,
            action=args.action,
            agent_name=args.agent_name,
            run_id=args.run_id,
            cost_usd=args.cost_usd,
            duration_s=args.duration_s,
            label_applied=args.label_applied,
            risk_class=args.risk_class,
            confidence=args.confidence,
            outcome=args.outcome,
            pr_url=args.pr_url,
        )
        print(f"appended row -> {args.csv_path}")
    elif args.subcommand == "tail":
        rows = FactoryLog(args.csv_path).rows()
        print(f"rows: {len(rows)}")
        for r in rows[-10:]:
            print(r)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
