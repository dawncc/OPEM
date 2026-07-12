import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from memory_common.config import get_settings
from memory_server.db import SessionLocal
from memory_server.models import DailySummary, Memory, MemorySource, Observation, ProcessingJob, Project
from memory_server.services import tokenize


def tokens(text: str) -> set[str]:
    return set(tokenize(text))


def similarity(left: str, right: str) -> float:
    a, b = tokens(left), tokens(right)
    return len(a & b) / max(len(a | b), 1)


def memory_type_for(observation: Observation) -> str:
    """Keep tool successes and failure FAQs in separate consolidation domains."""
    tool_status = (observation.metadata_ or {}).get("tool_status")
    if tool_status == "success":
        return "tool_success"
    if tool_status == "failed":
        return "tool_failure_faq"
    return observation.kind


def rule_compress(observation: Observation) -> tuple[str, str]:
    clean = " ".join(observation.content.strip().split())
    title = clean.split("。", 1)[0].split("\n", 1)[0][:100]
    if not title:
        title = f"{observation.kind} memory"
    labels = {
        "decision": "决策", "learning": "经验", "problem": "问题", "solution": "解决方案",
        "session_summary": "会话总结", "observation": "观察",
    }
    content = f"类型：{labels.get(observation.kind, observation.kind)}\n\n{observation.content.strip()}"
    if observation.files:
        content += "\n\n相关文件：\n" + "\n".join(f"- {path}" for path in observation.files)
    return title, content


def llm_compress(observation: Observation) -> tuple[str, str] | None:
    settings = get_settings()
    if not settings.memory_llm_enabled:
        return None
    prompt = (
        "将以下 Codex 观察压缩为可长期检索的 Memory。第一行输出不超过100字标题，之后输出正文；"
        "正文说明结论、背景、原因、适用范围和未解决问题。不得添加原文没有的事实。\n\n"
        f"类型: {observation.kind}\n内容:\n{observation.content}"
    )
    response = httpx.post(
        f"{settings.memory_llm_base_url.rstrip('/')}/chat/completions",
        json={"model": settings.memory_llm_model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1},
        timeout=60,
    )
    response.raise_for_status()
    result = response.json()["choices"][0]["message"]["content"].strip()
    title, _, body = result.partition("\n")
    return title.strip("# ")[:300], (body.strip() or result)


class Embedder:
    def __init__(self) -> None:
        self.model = None

    def encode(self, text: str) -> list[float] | None:
        settings = get_settings()
        if not settings.memory_embedding_enabled:
            return None
        if self.model is None:
            from sentence_transformers import SentenceTransformer
            self.model = SentenceTransformer(settings.memory_embedding_model)
        return self.model.encode(text, normalize_embeddings=True).tolist()


embedder = Embedder()


def local_date(value: datetime, timezone_name: str):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo(timezone_name)).date()


def build_daily_content(project_name: str, day, observations: list[Observation], memories: list[Memory]) -> tuple[str, list[str], list[str], list[str]]:
    decisions = [item.content.strip() for item in observations if item.kind == "decision"][:10]
    learnings = [item.content.strip() for item in observations if item.kind in {"learning", "solution"}][:10]
    unresolved = [item.content.strip() for item in observations if item.kind == "problem"][:10]
    highlights = sorted(memories, key=lambda item: (item.importance, item.updated_at), reverse=True)[:8]
    sections = [
        f"{project_name} 在 {day.isoformat()} 共记录 {len(observations)} 条 Observation，形成或更新 {len(memories)} 条 Memory。"
    ]
    for title, values in (("关键决策", decisions), ("经验与解决方案", learnings), ("未解决问题", unresolved)):
        if values:
            sections.append(title + "：\n" + "\n".join(f"- {value}" for value in values))
    if highlights:
        sections.append("Memory 要点：\n" + "\n".join(f"- {item.title}" for item in highlights))
    return "\n\n".join(sections), decisions, learnings, unresolved


def refresh_daily_summaries() -> int:
    settings = get_settings()
    changed = 0
    with SessionLocal() as db:
        projects = db.scalars(select(Project)).all()
        for project in projects:
            observations = db.scalars(select(Observation).where(Observation.project_id == project.id)).all()
            memories = db.scalars(select(Memory).where(Memory.project_id == project.id)).all()
            days = {local_date(item.created_at, settings.memory_timezone) for item in observations}
            days.update(local_date(item.updated_at, settings.memory_timezone) for item in memories)
            for day in days:
                day_observations = [item for item in observations if local_date(item.created_at, settings.memory_timezone) == day]
                day_memories = [item for item in memories if local_date(item.updated_at, settings.memory_timezone) == day]
                content, decisions, learnings, unresolved = build_daily_content(project.name, day, day_observations, day_memories)
                summary = db.scalar(select(DailySummary).where(DailySummary.project_id == project.id, DailySummary.summary_date == day))
                if summary is None:
                    summary = DailySummary(project_id=project.id, summary_date=day, title=f"{day.isoformat()} 每日记忆总结", content=content)
                    db.add(summary)
                summary.content = content
                summary.observation_count = len(day_observations)
                summary.memory_count = len(day_memories)
                summary.decisions = decisions
                summary.learnings = learnings
                summary.unresolved = unresolved
                changed += 1
        db.commit()
    return changed


def process(job_id) -> None:
    with SessionLocal() as db:
        job = db.get(ProcessingJob, job_id)
        observation = db.get(Observation, job.observation_id) if job else None
        if not job or not observation:
            return
        try:
            compressed = llm_compress(observation) or rule_compress(observation)
            title, content = compressed
            memory_type = memory_type_for(observation)
            search_text = " ".join([title, content, *(observation.concepts or []), *(observation.files or [])])
            candidates = db.scalars(
                select(Memory)
                .where(Memory.project_id == observation.project_id, Memory.memory_type == memory_type)
                .order_by(Memory.updated_at.desc())
                .limit(100)
            ).all()
            best = max(candidates, key=lambda item: similarity(item.search_text, search_text), default=None)
            best_score = similarity(best.search_text, search_text) if best else 0
            if best and best_score >= 0.58:
                memory = best
                if observation.content not in memory.content:
                    memory.content = f"{memory.content}\n\n---\n\n{content}"
                memory.title = title if observation.importance >= memory.importance else memory.title
                memory.concepts = sorted(set((memory.concepts or []) + (observation.concepts or [])))
                memory.files = sorted(set((memory.files or []) + (observation.files or [])))
                memory.importance = max(memory.importance, observation.importance)
                memory.confidence = min(0.98, memory.confidence + 0.03)
                memory.search_text = " ".join([memory.title, memory.content, *memory.concepts, *memory.files])
                relation = "reinforces"
            else:
                memory = Memory(
                    project_id=observation.project_id, title=title, content=content,
                    memory_type=memory_type, concepts=observation.concepts or [], files=observation.files or [],
                    importance=observation.importance, confidence=0.7, search_text=search_text,
                )
                db.add(memory)
                db.flush()
                relation = "derived_from"
            existing = db.scalar(select(MemorySource).where(MemorySource.memory_id == memory.id, MemorySource.observation_id == observation.id))
            if not existing:
                db.add(MemorySource(memory_id=memory.id, observation_id=observation.id, relation=relation))
            memory.embedding = embedder.encode(memory.search_text)
            observation.processed_at = datetime.now(timezone.utc)
            job.status = "completed"
            job.finished_at = datetime.now(timezone.utc)
            job.error = None
            db.commit()
        except Exception as exc:
            job.status = "pending" if job.attempts < 3 else "failed"
            job.error = str(exc)[:4000]
            job.finished_at = datetime.now(timezone.utc)
            db.commit()


def claim_one():
    with SessionLocal() as db:
        stale = datetime.now(timezone.utc) - timedelta(minutes=10)
        db.execute(update(ProcessingJob).where(ProcessingJob.status == "processing", ProcessingJob.started_at < stale).values(status="pending"))
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.status == "pending").order_by(ProcessingJob.created_at).with_for_update(skip_locked=True).limit(1))
        if not job:
            db.commit()
            return None
        job.status = "processing"
        job.started_at = datetime.now(timezone.utc)
        job.attempts += 1
        job_id = job.id
        db.commit()
        return job_id


def run() -> None:
    settings = get_settings()
    delay = settings.memory_worker_poll_seconds
    next_daily_refresh = 0.0
    while True:
        if time.monotonic() >= next_daily_refresh:
            try:
                refresh_daily_summaries()
            finally:
                next_daily_refresh = time.monotonic() + settings.memory_daily_refresh_seconds
        job_id = claim_one()
        if job_id:
            process(job_id)
        else:
            time.sleep(delay)


if __name__ == "__main__":
    run()
