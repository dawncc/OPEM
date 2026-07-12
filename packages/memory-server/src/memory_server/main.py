from pathlib import Path
import calendar as calendar_module
from contextlib import asynccontextmanager
from datetime import date, datetime
from uuid import UUID

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session as DbSession, selectinload

from memory_common.config import get_settings
from memory_common.schemas import ChatBatchCreate, ChatBatchResponse, HealthResponse, MemoryFeedbackCreate, MemoryFeedbackResponse, ObservationCreate, RecallRequest, RecallResponse, SubmitResponse

from .db import Base, SessionLocal, engine, get_db
from .conversation import build_chat_turns, session_display_title
from .daily_archive import render_daily_markdown
from .models import ChatMessage, DailySummary, Memory, MemorySource, Observation, ProcessingJob, Project, Session, ToolExecution
from .services import create_chat_batch, create_memory_feedback, create_observation, format_recall_context, recall
from .tracing import build_session_trace, format_duration


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    from .tool_archive import backfill_tool_archives
    with SessionLocal() as db:
        backfill_tool_archives(db)
    yield


app = FastAPI(
    title="OPEM",
    description="One Personal Evolving Memory System",
    version="0.1.0",
    lifespan=lifespan,
)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def pretty_datetime(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "—"


templates.env.filters["pretty_datetime"] = pretty_datetime
templates.env.filters["duration"] = format_duration


@app.get("/api/v1/health", response_model=HealthResponse)
def health(db: DbSession = Depends(get_db)) -> HealthResponse:
    try:
        db.execute(text("SELECT 1"))
        pending = db.scalar(select(func.count()).select_from(ProcessingJob).where(ProcessingJob.status == "pending")) or 0
        return HealthResponse(status="ok", database="ok", pending_jobs=pending)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/v1/observations", response_model=SubmitResponse, status_code=202)
def observations(payload: ObservationCreate, db: DbSession = Depends(get_db)) -> SubmitResponse:
    observation, duplicate = create_observation(db, payload)
    return SubmitResponse(observation_id=observation.id, session_id=observation.session_id, duplicate=duplicate)


@app.post("/api/v1/chat/messages", response_model=ChatBatchResponse, status_code=202)
def chat_messages(payload: ChatBatchCreate, db: DbSession = Depends(get_db)) -> ChatBatchResponse:
    session, accepted, duplicates = create_chat_batch(db, payload)
    return ChatBatchResponse(session_id=session.id, accepted=accepted, duplicates=duplicates)


@app.post("/api/v1/recall", response_model=RecallResponse)
def recall_api(payload: RecallRequest, db: DbSession = Depends(get_db)) -> RecallResponse:
    items = recall(db, payload.query, payload.project, payload.limit)
    return RecallResponse(items=items, context=format_recall_context(payload.query, items))


@app.post("/api/v1/memories/feedback", response_model=MemoryFeedbackResponse, status_code=202)
def memory_feedback(payload: MemoryFeedbackCreate, db: DbSession = Depends(get_db)) -> MemoryFeedbackResponse:
    try:
        item, memory, counts, duplicate = create_memory_feedback(db, payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return MemoryFeedbackResponse(
        feedback_id=item.id, memory_id=memory.id, duplicate=duplicate,
        helpful=counts.get("helpful", 0), irrelevant=counts.get("irrelevant", 0),
        harmful=counts.get("harmful", 0), confidence=memory.confidence,
    )


@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: DbSession = Depends(get_db)):
    counts = {
        "projects": db.scalar(select(func.count()).select_from(Project)) or 0,
        "sessions": db.scalar(select(func.count()).select_from(Session)) or 0,
        "messages": db.scalar(select(func.count()).select_from(ChatMessage)) or 0,
        "observations": db.scalar(select(func.count()).select_from(Observation)) or 0,
        "memories": db.scalar(select(func.count()).select_from(Memory)) or 0,
        "pending": db.scalar(select(func.count()).select_from(ProcessingJob).where(ProcessingJob.status == "pending")) or 0,
    }
    projects = db.scalars(select(Project).order_by(Project.created_at.desc()).limit(20)).all()
    observations = db.execute(select(Observation, Project).join(Project).order_by(Observation.created_at.desc()).limit(15)).all()
    memories = db.execute(select(Memory, Project).join(Project).order_by(Memory.updated_at.desc()).limit(15)).all()
    recent_sessions = db.execute(
        select(Session, Project, func.max(ChatMessage.created_at), func.count(ChatMessage.id))
        .options(selectinload(Session.chat_messages))
        .join(Project, Session.project_id == Project.id)
        .outerjoin(ChatMessage, ChatMessage.session_id == Session.id)
        .group_by(Session.id, Project.id)
        .order_by(func.coalesce(func.max(ChatMessage.created_at), Session.started_at).desc())
        .limit(12)
    ).all()
    recent_sessions = [(*row, session_display_title(row[0])) for row in recent_sessions]
    return templates.TemplateResponse(request, "index.html", {
        "counts": counts, "projects": projects, "observations": observations,
        "memories": memories, "recent_sessions": recent_sessions,
    })


@app.get("/projects/{project_id}", response_class=HTMLResponse)
def project_page(request: Request, project_id: UUID, db: DbSession = Depends(get_db)):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404)
    sessions = db.scalars(select(Session).where(Session.project_id == project_id).order_by(Session.started_at.desc()).limit(30)).all()
    observations = db.scalars(select(Observation).where(Observation.project_id == project_id).order_by(Observation.created_at.desc()).limit(50)).all()
    memories = db.scalars(select(Memory).where(Memory.project_id == project_id).order_by(Memory.updated_at.desc()).limit(50)).all()
    return templates.TemplateResponse(request, "project.html", {"project": project, "sessions": sessions, "observations": observations, "memories": memories})


@app.get("/traces", response_class=HTMLResponse)
def traces_page(request: Request, project: str | None = None, db: DbSession = Depends(get_db)):
    stmt = (
        select(Session)
        .options(selectinload(Session.project), selectinload(Session.chat_messages))
        .order_by(Session.started_at.desc())
    )
    if project:
        stmt = stmt.join(Project).where(Project.name == project)
    sessions = db.scalars(stmt.limit(100)).all()
    traces = [build_session_trace(session) for session in sessions]
    projects = db.scalars(select(Project).order_by(Project.name)).all()
    summary = {
        "sessions": len(traces),
        "requests": sum(trace["request_count"] for trace in traces),
        "tools": sum(trace["tool_count"] for trace in traces),
        "failed": sum(trace["failed_count"] for trace in traces),
        "duration_ms": sum(trace["active_duration_ms"] or 0 for trace in traces),
    }
    return templates.TemplateResponse(request, "traces.html", {"traces": traces, "projects": projects, "selected_project": project, "summary": summary})


@app.get("/traces/{session_id}", response_class=HTMLResponse)
def trace_page(request: Request, session_id: UUID, db: DbSession = Depends(get_db)):
    session = db.scalar(
        select(Session)
        .where(Session.id == session_id)
        .options(selectinload(Session.project), selectinload(Session.chat_messages))
    )
    if not session:
        raise HTTPException(404)
    return templates.TemplateResponse(request, "trace.html", {"trace": build_session_trace(session)})


@app.get("/sessions/{session_id}", response_class=HTMLResponse)
def session_page(request: Request, session_id: UUID, db: DbSession = Depends(get_db)):
    session = db.scalar(select(Session).where(Session.id == session_id).options(selectinload(Session.project), selectinload(Session.observations), selectinload(Session.chat_messages), selectinload(Session.tool_executions)))
    if not session:
        raise HTTPException(404)
    memories = db.scalars(select(Memory).join(MemorySource).join(Observation).where(Observation.session_id == session_id).distinct()).all()
    chat_turns = build_chat_turns(session.chat_messages)
    return templates.TemplateResponse(request, "session.html", {"session": session, "memories": memories, "chat_turns": chat_turns, "user_turn_count": sum(not turn["is_context"] for turn in chat_turns)})


@app.get("/tools", response_class=HTMLResponse)
def tools_page(request: Request, status: str | None = None, db: DbSession = Depends(get_db)):
    stmt = select(ToolExecution).options(selectinload(ToolExecution.project), selectinload(ToolExecution.session)).order_by(ToolExecution.created_at.desc())
    if status in {"success", "failed", "unknown"}:
        stmt = stmt.where(ToolExecution.status == status)
    executions = db.scalars(stmt.limit(200)).all()
    counts = {key: db.scalar(select(func.count()).select_from(ToolExecution).where(ToolExecution.status == key)) or 0 for key in ("success", "failed", "unknown")}
    return templates.TemplateResponse(request, "tools.html", {"executions": executions, "selected_status": status, "counts": counts})


@app.get("/faq", response_class=HTMLResponse)
def faq_page(request: Request, db: DbSession = Depends(get_db)):
    executions = db.scalars(select(ToolExecution).where(ToolExecution.status == "failed").options(selectinload(ToolExecution.project), selectinload(ToolExecution.session)).order_by(ToolExecution.created_at.desc()).limit(200)).all()
    return templates.TemplateResponse(request, "faq.html", {"executions": executions})


@app.get("/memories/{memory_id}", response_class=HTMLResponse)
def memory_page(request: Request, memory_id: UUID, db: DbSession = Depends(get_db)):
    memory = db.scalar(select(Memory).where(Memory.id == memory_id).options(selectinload(Memory.sources).selectinload(MemorySource.observation)))
    if not memory:
        raise HTTPException(404)
    project = db.get(Project, memory.project_id)
    return templates.TemplateResponse(request, "memory.html", {"memory": memory, "project": project})


@app.get("/search", response_class=HTMLResponse)
def search_page(request: Request, q: str = Query(default=""), project: str | None = None, db: DbSession = Depends(get_db)):
    results = recall(db, q, project, 50) if q.strip() else []
    projects = db.scalars(select(Project).order_by(Project.name)).all()
    return templates.TemplateResponse(request, "search.html", {"q": q, "selected_project": project, "projects": projects, "results": results})


@app.get("/calendar", response_class=HTMLResponse)
def calendar_page(request: Request, year: int | None = None, month: int | None = None, db: DbSession = Depends(get_db)):
    today = date.today()
    year = year or today.year
    month = month or today.month
    if not 1 <= month <= 12 or not 2000 <= year <= 2100:
        raise HTTPException(400, "Invalid calendar month")
    summaries = db.scalars(select(DailySummary).where(
        DailySummary.summary_date >= date(year, month, 1),
        DailySummary.summary_date <= date(year, month, calendar_module.monthrange(year, month)[1]),
    ).options(selectinload(DailySummary.project))).all()
    by_day: dict[int, list[DailySummary]] = {}
    for summary in summaries:
        by_day.setdefault(summary.summary_date.day, []).append(summary)
    weeks = calendar_module.Calendar(firstweekday=0).monthdayscalendar(year, month)
    previous = date(year - 1, 12, 1) if month == 1 else date(year, month - 1, 1)
    following = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return templates.TemplateResponse(request, "calendar.html", {"year": year, "month": month, "weeks": weeks, "by_day": by_day, "previous": previous, "following": following, "today": today})


@app.get("/calendar/{summary_date}", response_class=HTMLResponse)
def daily_page(request: Request, summary_date: date, db: DbSession = Depends(get_db)):
    summaries = db.scalars(select(DailySummary).where(DailySummary.summary_date == summary_date).options(selectinload(DailySummary.project))).all()
    return templates.TemplateResponse(request, "daily.html", {"summary_date": summary_date, "summaries": summaries})


@app.get("/calendar/{summary_date}/export.md")
def export_daily_markdown(
    summary_date: date,
    project_id: UUID | None = None,
    db: DbSession = Depends(get_db),
):
    stmt = (
        select(DailySummary)
        .where(DailySummary.summary_date == summary_date)
        .options(selectinload(DailySummary.project))
    )
    if project_id is not None:
        stmt = stmt.where(DailySummary.project_id == project_id)
    summaries = db.scalars(stmt).all()
    if project_id is not None and not summaries:
        raise HTTPException(404, "Daily summary not found")
    filename = f"daily-work-summary-{summary_date.isoformat()}"
    if project_id is not None:
        filename += f"-{project_id}"
    return Response(
        render_daily_markdown(summary_date, summaries),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}.md"'},
    )


def run() -> None:
    settings = get_settings()
    uvicorn.run("memory_server.main:app", host=settings.memory_server_host, port=settings.memory_server_port)
