"""Backfill TaskRun and ExecutionNode records from archived sessions."""

import argparse
import json

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from memory_server.db import SessionLocal
from memory_server.models import Project, Session
from memory_server.trajectory import sync_session_task_runs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as db:
        stmt = select(Session).options(selectinload(Session.project)).order_by(Session.started_at)
        if args.project:
            stmt = stmt.join(Project).where(Project.name == args.project)
        sessions = db.scalars(stmt).all()
        tasks = []
        for session in sessions:
            tasks.extend(sync_session_task_runs(db, session))
        unique_tasks = {task.id: task for task in tasks}
        output = {
            "project": args.project,
            "sessions": len(sessions),
            "task_runs": len(unique_tasks),
            "completed": sum(task.status == "completed" for task in unique_tasks.values()),
            "dry_run": args.dry_run,
        }
        db.rollback() if args.dry_run else db.commit()
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
