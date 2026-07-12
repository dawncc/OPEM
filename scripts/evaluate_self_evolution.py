"""Replay explicit feedback against baseline and candidate recall policies."""

import argparse
import json
from dataclasses import asdict

from sqlalchemy.exc import OperationalError, ProgrammingError

from memory_server.db import SessionLocal
from memory_server.evolution import feedback_cases, evaluate_policy, promotion_allowed, search_policies
from memory_server.services import DEFAULT_RECALL_POLICY


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--min-cases", type=int, default=20)
    args = parser.parse_args()
    try:
        with SessionLocal() as db:
            cases = feedback_cases(db, args.project)
            baseline = evaluate_policy(db, cases, DEFAULT_RECALL_POLICY, args.k)
            candidates = search_policies(db, cases, args.k)
            best = candidates[0] if candidates else None
            output = {
                "dataset": {"source": "explicit_feedback", "cases": len(cases), "k": args.k},
                "baseline": {"policy": asdict(DEFAULT_RECALL_POLICY), "metrics": asdict(baseline)},
                "candidate": None if best is None else {
                    "policy": asdict(best.policy), "metrics": asdict(best.metrics),
                    "promotion_allowed": promotion_allowed(baseline, best.metrics, args.min_cases),
                },
            }
    except (OperationalError, ProgrammingError) as exc:
        raise SystemExit("Feedback tables are unavailable; apply the latest Alembic migrations first.") from exc
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
