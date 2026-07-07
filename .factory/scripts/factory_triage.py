#!/usr/bin/env python3
"""Deterministic triage classifier — local dry-run of the triage SKILL.

Mirrors skills/triage/SKILL.md sections 1 (risk gate), 2 (routing),
3 (confidence), and emits the strict output JSON from section 0.

Intended use:
    python3 .factory/scripts/factory_triage.py --issue-num 42 \
        --title '...' --body '...' [--model-tier local]

This is **not** a production classifier; production triage is run by
the Warp/Oz agent invoked from .github/workflows/factory-triage.yml.
This script exists so the week-1 eval harness can score the SKILL
without paying cloud-agent dollars.

Side effects: none. No network calls.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

# ---------------------------------------------------------------------------
# Risk path tables (mirrors .factory/config.yaml forbidden_globs + skill 1a-d)
# ---------------------------------------------------------------------------
RED_PATHS = (
    "src/asusroutercontrol/credentials.py",
    "src/asusroutercontrol/ssh.py",
    "pyproject.toml",
    "uv.lock",
    ".github/workflows",
    "Makefile",
    "scripts/build_macos_app.sh",
    "scripts/verify_dev_app.sh",
)
HIGH_PATHS = (
    "src/asusroutercontrol/backends/",
    "src/asusroutercontrol/models.py",
    "src/asusroutercontrol/datastore.py",
)
MEDIUM_PATHS = (
    "src/asusroutercontrol/analyzer.py",
    "src/asusroutercontrol/optimizer.py",
    "src/asusroutercontrol/reporting.py",
    "src/asusroutercontrol/probes.py",
)
# dhcp_*.py and speedtest*.py are also Medium per SKILL §1c
MEDIUM_GLOBS = (
    re.compile(r"src/asusroutercontrol/dhcp_[^/]*\.py"),
    re.compile(r"src/asusroutercontrol/speedtest[^/]*\.py"),
)
LOW_PATHS = (
    "tests/",
    "docs/",
    "src/asusroutercontrol/notifications.py",
)
MACOS_TRIGGER_FILES = (
    "src/asusroutercontrol/menubar.py",
    "scripts/build_macos_app.sh",
    "scripts/verify_dev_app.sh",
)

# Body keywords that trigger critical risk per SKILL §1a
RED_KEYWORDS = (
    "credential", "credentials", "password", "passwords",
    "api key", "api keys", "oauth token", "oauth tokens",
    "1password", "ssh host key", "ssh host-key", "ssh host keys",
    "payment", "payments", "production deploy", "production deploys",
)

VAGUE_PHRASES = (
    "should look at", "investigate sometime", "eventually",
    "make it better", "make it nicer",
)


# ---------------------------------------------------------------------------
# Heuristic helpers
# ---------------------------------------------------------------------------
def _has_file_hits(text: str) -> list[str]:
    """Pull repo-relative path-shaped tokens out of free text."""
    pat = re.compile(
        r"(?:^|[\s`])([A-Za-z0-9_./-]+\.(?:py|md|ya?ml|jsonl?|sh|toml|lock))"
    )
    out: list[str] = []
    seen: set[str] = set()
    for m in pat.finditer(text):
        path = m.group(1)
        if path not in seen:
            seen.add(path)
            out.append(path)
    return out


def _body_completeness(title: str, body: str) -> tuple[float, dict[str, bool]]:
    """SKILL §2 Body completeness formula.

    Score = (1/3) * sum of three boolean signals. Mirrors SKILL heuristics:
      - has_acceptance_criteria: matches 'Expected:'/'Acceptance:'/'Should:'
        OR a numbered behavior list ('1)'/'1.') at line start.
      - has_explicit_file_paths: any repo-rooted path (src/|tests/|docs/|scripts/|
        .github/|pyproject) OR any RED_PATHS token OR a path-shaped file ext
        (.py/.md/.yml/.yaml/.jsonl/.json/.sh/.toml/.lock) appearing anywhere.
      - has_reproduction_steps: matches 'Repro:'/'Steps:'/'Expected:' or
        fenced ```sh/```bash block, or numbered steps.

    The score is the spec's literal formula: three booleans summed and
    divided by 3, NOT a partial-credit sum.
    """
    blob = title + "\n" + body
    has_repo_path = bool(
        re.search(
            r"\b(?:src|tests|docs|scripts|\.github)/[A-Za-z0-9_./-]*\.(?:py|md|ya?ml|jsonl?|sh|toml|lock)\b",
            blob,
        )
        or re.search(r"\bpyproject\.toml\b", blob)
    )
    has_any_path = bool(
        re.search(
            r"\b[A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:py|md|ya?ml|jsonl?|sh|toml|lock)\b",
            blob,
        )
    )
    has_red = any(p in blob for p in RED_PATHS)

    flags = {
        "has_acceptance_criteria": bool(
            re.search(r"\b(Expected:|Acceptance:|Should:)\b", body, re.IGNORECASE)
            or re.search(r"^(\s*)\d+[\.\)]\s", body, re.MULTILINE)
        ),
        "has_explicit_file_paths": has_repo_path or has_red or has_any_path,
        "has_reproduction_steps": bool(
            re.search(r"\b(Repro:|Steps:|Expected:|Actual:)\b", body, re.IGNORECASE)
            or re.search(r"^(\s*)\d+[\.\)]\s", body, re.MULTILINE)
            or "```sh" in body or "```bash" in body
        ),
    }
    score = sum(1.0 for v in flags.values() if v) / 3.0
    return score, flags


def _vague(title: str, body: str, completeness: float, file_hits: list[str]) -> bool:
    if completeness >= 0.66:
        return False
    txt = (title + "\n" + body).lower()
    if any(p in txt for p in VAGUE_PHRASES):
        return True
    # No file paths, no acceptance criteria, body is short
    if not file_hits and len(body.strip()) < 200:
        return True
    return False


def _classify_risk(
    file_hits: list[str],
    body_text: str,
) -> tuple[str, list[str]]:
    """Return (risk_class, matched_rules). First match wins."""
    matched: list[str] = []
    blob = " ".join(file_hits) + " " + body_text

    for path in RED_PATHS:
        if path in blob:
            matched.append(f"red_path:{path}")
    if any(p in txt(blob) for p in RED_KEYWORDS) and not matched:
        matched.append("red_keyword")
    if matched:
        return "critical", matched

    for path in HIGH_PATHS:
        if path in blob:
            matched.append(f"high_path:{path}")
    if matched:
        return "high", matched

    for path in MEDIUM_PATHS:
        if path in blob:
            matched.append(f"medium_path:{path}")
    for rgx in MEDIUM_GLOBS:
        for hit in file_hits:
            if rgx.search(hit):
                matched.append(f"medium_glob:{hit}")
    if matched:
        return "medium", matched

    for path in LOW_PATHS:
        if path in blob:
            matched.append(f"low_path:{path}")
    if matched:
        return "low", matched

    # Default: short doc-only or typo work counts as low.
    return "low", ["default:low"]


def txt(s: str) -> str:
    return s.lower()


# ---------------------------------------------------------------------------
# LOC + scope estimate (very rough; deterministic).
# ---------------------------------------------------------------------------
def _loc_estimate(
    risk_class: str,
    file_hits: list[str],
    body_text: str,
    macos_rebuild: bool,
) -> int:
    base = 20
    base += 40 * len(file_hits)
    if risk_class == "high":
        base += 80
    if risk_class == "critical":
        base += 60
    if macos_rebuild:
        base += 60
    # Words in body as a weak proxy for narrative span (cap at 200)
    base += min(200, len(body_text.split()) // 8)
    return min(800, base)


# ---------------------------------------------------------------------------
# Confidence: SKILL §3 formula
# ---------------------------------------------------------------------------
def _confidence(
    completeness: float, loc: int, risk_class: str, model_tier: str
) -> float:
    raw = (
        0.5
        + 0.5 * completeness
        - 0.3 * (1 if loc > 100 else 0)
        - 0.2 * (1 if risk_class in ("high", "critical") else 0)
        + 0.1 * (1 if model_tier == "cloud" else 0)
    )
    conf = max(0.0, min(1.0, raw))

    # Escalation tier (SKILL §3 last paragraph): if confidence < 0.5,
    # we re-score once at tier=cloud. Since we are already deterministic,
    # we model that by re-evaluating with the cloud bonus BUILT IN.
    # If still < 0.5 after escalation, caller flips output via routing.
    return conf


# ---------------------------------------------------------------------------
# MacOS-trigger detection (SKILL §1e)
# ---------------------------------------------------------------------------
def _macos_rebuild(file_hits: list[str], body_text: str) -> bool:
    blob = " ".join(file_hits) + " " + body_text
    return any(p in blob for p in MACOS_TRIGGER_FILES)


# ---------------------------------------------------------------------------
# Main classifier (entry)
# ---------------------------------------------------------------------------
def classify(
    issue_number: int,
    issue_title: str,
    issue_body: str,
    repo_root_path: str = ".",
    model_tier: str = "local",
) -> dict[str, Any]:
    """Return the strict triage output JSON.

    Mirrors skills/triage/SKILL.md §0 outputs schema verbatim.
    """
    title = issue_title or ""
    body = (issue_body or "")[:2000]
    file_hits = _has_file_hits(title + "\n" + body)
    completeness, comp_flags = _body_completeness(title, body)
    macos_rebuild = _macos_rebuild(file_hits, body)
    risk_class, risk_matched = _classify_risk(file_hits, body)
    loc = _loc_estimate(risk_class, file_hits, body, macos_rebuild)

    # ----- routing (SKILL §2) -----
    red_blob = txt(" ".join(file_hits) + " " + body)
    if risk_class == "critical" or any(k in red_blob for k in RED_KEYWORDS):
        label = "needs_human"
    elif macos_rebuild and loc > 200:
        label = "ready_to_spec"
    elif loc > 200:
        label = "ready_to_spec"
    elif completeness < 0.66:
        label = "needs_info"
    elif _vague(title, body, completeness, file_hits):
        label = "wait_to_implement"
    else:
        label = "ready_to_implement"

    conf_local = _confidence(completeness, loc, risk_class, "local")
    conf_cloud = _confidence(completeness, loc, risk_class, "cloud")
    confidence = conf_local
    if confidence < 0.5:
        # one-shot escalation
        confidence = conf_cloud
    if confidence < 0.7:
        # Confidence is a first-class signal (SKILL §2 last paragraph)
        label = "needs_info"

    files_likely = list(dict.fromkeys(file_hits)) or ["(none-detected)"]

    return {
        "issue_number": issue_number,
        "label": label,
        "risk_class": risk_class,
        "confidence": round(confidence, 3),
        "scope_estimate": {
            "files_likely_touched": files_likely,
            "loc_added_estimate": loc,
        },
        "macos_app_rebuild_required": macos_rebuild,
        "body_completeness_score": round(completeness, 3),
        "reasoning_short": (
            f"risk={risk_class}; {'; '.join(risk_matched)[:120]}; "
            f"loc~{loc}; comp={completeness:.2f}; "
            f"{macos_rebuild and 'macos_rebuild' or 'no_macos'}"
        )[:240],
        # diagnostic extensions (do not affect Implement Skill trust):
        "_diag": {
            "completeness_flags": comp_flags,
            "model_tier_used": "cloud" if conf_local < 0.5 else model_tier,
            "file_hits": file_hits,
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _cli() -> int:
    p = argparse.ArgumentParser(description="Deterministic triage dry-run")
    p.add_argument("--issue-num", type=int, required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--body", default="")
    p.add_argument("--repo-root", default=".")
    p.add_argument("--model-tier", choices=("local", "cloud"), default="local")
    args = p.parse_args()
    out = classify(
        issue_number=args.issue_num,
        issue_title=args.title,
        issue_body=args.body,
        repo_root_path=args.repo_root,
        model_tier=args.model_tier,
    )
    json.dump(out, sys.stdout, indent=2, sort_keys=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
