"""Markdown archive rendering for daily work summaries."""

from datetime import date, datetime
from typing import Iterable

from .models import DailySummary


def _list_section(title: str, items: list[str]) -> list[str]:
    if not items:
        return []
    return [f"### {title}", "", *(f"- {item}" for item in items), ""]


def render_daily_markdown(
    summary_date: date,
    summaries: Iterable[DailySummary],
    *,
    generated_at: datetime | None = None,
) -> str:
    """Render one day's project summaries as a portable Markdown archive."""
    items = sorted(summaries, key=lambda item: item.project.name.casefold())
    generated_at = generated_at or datetime.now().astimezone()
    observation_count = sum(item.observation_count for item in items)
    memory_count = sum(item.memory_count for item in items)
    lines = [
        f"# {summary_date.isoformat()} 每日工作汇总",
        "",
        f"> 共 {len(items)} 个项目，{observation_count} 条工作记录，{memory_count} 条长期记忆。",
        "",
    ]
    if not items:
        lines.extend(["当天暂无工作记录。", ""])
    for item in items:
        lines.extend([
            f"## {item.project.name}",
            "",
            f"- 工作记录：{item.observation_count} 条",
            f"- 长期记忆：{item.memory_count} 条",
            f"- 最后更新：{item.updated_at.isoformat(timespec='minutes')}",
            "",
            item.content.strip(),
            "",
        ])
        lines.extend(_list_section("关键决策", item.decisions or []))
        lines.extend(_list_section("经验与解决方案", item.learnings or []))
        lines.extend(_list_section("未解决事项", item.unresolved or []))
    lines.extend(["---", "", f"归档生成时间：{generated_at.isoformat(timespec='minutes')}", ""])
    return "\n".join(lines)
