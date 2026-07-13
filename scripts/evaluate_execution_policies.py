"""Evaluate observed execution strategies without auto-promoting them."""

import argparse
import json

from memory_server.db import SessionLocal
from memory_server.strategy import evaluate_execution_strategies, evolution_summary, propose_shadow_strategy


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project")
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--propose-shadow", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as db:
        candidate = reasons = None
        if args.propose_shadow:
            if not args.project:
                raise SystemExit("--propose-shadow requires --project")
            strategy, reasons = propose_shadow_strategy(db, args.project)
            candidate = None if strategy is None else {"id": str(strategy.id), "version": strategy.version, "status": strategy.status, "config": strategy.config}
        output = {
            "summary": evolution_summary(db, args.project),
            "policies": evaluate_execution_strategies(db, args.project, persist=args.persist),
            "automatic_promotion": False,
            "shadow_candidate": candidate,
            "shadow_reasons": reasons,
        }
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
