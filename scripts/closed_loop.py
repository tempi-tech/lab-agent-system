#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
from typing import Any, Callable, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass(frozen=True)
class ScorecardResult:
    feature: str
    score: float
    failing_cases: list[str]
    notes: list[str]

    def to_json(self) -> dict[str, Any]:
        return {
            "feature": self.feature,
            "score": self.score,
            "failing_cases": list(self.failing_cases),
            "notes": list(self.notes),
        }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _feature_to_scorecard_import(feature: str) -> str:
    # Convention: each feature exposes `evaluate()` returning ScorecardResult-like dict.
    if feature == "question_sla":
        return "src.agents.question_sla.scorecard"
    if feature == "membership":
        return "src.agents.membership_checker.scorecard"
    if feature == "analytics":
        return "src.agents.community_analytics.scorecard"
    raise ValueError(f"Unknown feature: {feature}")


def _load_scorecard(feature: str) -> Callable[[], dict[str, Any]]:
    module_path = _feature_to_scorecard_import(feature)
    try:
        module = import_module(module_path)
    except Exception as exc:
        err = repr(exc)
        def _missing() -> dict[str, Any]:
            return ScorecardResult(
                feature=feature,
                score=0.0,
                failing_cases=["scorecard_module_missing"],
                notes=[f"Missing scorecard module: {module_path}", f"Import error: {err}"],
            ).to_json()

        return _missing

    fn = getattr(module, "evaluate", None)
    if not callable(fn):
        def _invalid() -> dict[str, Any]:
            return ScorecardResult(
                feature=feature,
                score=0.0,
                failing_cases=["scorecard_missing_evaluate_fn"],
                notes=[f"Scorecard module has no callable evaluate(): {module_path}"],
            ).to_json()

        return _invalid

    return fn  # type: ignore[return-value]


def cmd_eval(feature: str) -> dict[str, Any]:
    evaluator = _load_scorecard(feature)
    payload = evaluator()
    # Normalize required fields (be tolerant).
    payload.setdefault("feature", feature)
    payload.setdefault("score", 0.0)
    payload.setdefault("failing_cases", [])
    payload.setdefault("notes", [])
    return payload


def _run(cmd: list[str], *, env: dict[str, str] | None = None) -> int:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    proc = subprocess.run(cmd, cwd=str(ROOT), env=merged_env)
    return int(proc.returncode)


def _pytest_paths_for_feature(feature: str) -> list[str]:
    if feature == "question_sla":
        return ["tests/agents/question_sla", "tests/core/test_discord_text.py"]
    if feature == "membership":
        return ["tests/agents/membership_checker"]
    if feature == "analytics":
        return ["tests/agents/community_analytics", "tests/core/test_discord_history.py", "tests/core/test_discord_text.py"]
    raise ValueError(f"Unknown feature: {feature}")


def cmd_verify(feature: str) -> int:
    # 1) compileall
    rc = _run([sys.executable, "-m", "compileall", "src"])
    if rc != 0:
        return rc

    # 2) pytest (targeted paths)
    test_paths = _pytest_paths_for_feature(feature)
    existing: list[str] = []
    for path in test_paths:
        if (ROOT / path).exists():
            existing.append(path)
    rc = _run([sys.executable, "-m", "pytest", "-q", *existing] if existing else [sys.executable, "-m", "pytest", "-q"])
    if rc != 0:
        return rc

    # 3) mypy
    rc = _run([sys.executable, "-m", "mypy", "src"])
    return rc


def cmd_snapshot(feature: str, iteration: int) -> Path:
    now = _utc_now()
    payload = cmd_eval(feature)
    payload = dict(payload)
    payload["timestamp"] = now.isoformat()
    payload["iteration"] = iteration

    out_dir = ROOT / "data" / "closed_loop" / feature
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{now.strftime('%Y%m%d_%H%M%S')}_iter{iteration}.json"
    out_path = out_dir / filename
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Closed-loop scorecard CLI (offline).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_eval = sub.add_parser("eval", help="Evaluate a feature scorecard (offline).")
    p_eval.add_argument("feature", choices=["question_sla", "membership", "analytics"])

    p_verify = sub.add_parser("verify", help="Run compileall + pytest + mypy for a feature.")
    p_verify.add_argument("feature", choices=["question_sla", "membership", "analytics"])

    p_snapshot = sub.add_parser("snapshot", help="Evaluate and save scorecard JSON under data/closed_loop.")
    p_snapshot.add_argument("--feature", required=True, choices=["question_sla", "membership", "analytics"])
    p_snapshot.add_argument("--iteration", required=True, type=int)

    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    if args.cmd == "eval":
        print(json.dumps(cmd_eval(args.feature), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "verify":
        return cmd_verify(args.feature)
    if args.cmd == "snapshot":
        out_path = cmd_snapshot(args.feature, args.iteration)
        print(str(out_path))
        return 0
    raise SystemExit(f"Unknown cmd: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
