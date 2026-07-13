"""Recompute rule evidence and observable path scores for completed TaskRuns."""

import argparse
import json

from sqlalchemy import select

from memory_server.db import SessionLocal
from memory_server.models import TaskRun
from memory_server.outcomes import aggregate_task_quality, derive_rule_evidence
from memory_server.path_scoring import score_task_paths


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    with SessionLocal() as db:
        stmt = select(TaskRun).where(TaskRun.status == "completed").order_by(TaskRun.started_at)
        if args.limit > 0:
            stmt = stmt.limit(args.limit)
        tasks = db.scalars(stmt).all()
        evidence = scores = 0
        for task in tasks:
            evidence += len(derive_rule_evidence(db, task))
            aggregate_task_quality(db, task)
            scores += len(score_task_paths(db, task))
        db.commit()
    print(json.dumps({"tasks": len(tasks), "evidence_seen": evidence, "path_scores": scores}, indent=2))


if __name__ == "__main__":
    main()

