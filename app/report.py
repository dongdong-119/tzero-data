"""이번 실행의 변경 사항 → Slack 알림용 요약. 순수 함수만 (I/O 없음).

알릴 만한 변경만 고른다:
- 새 발사
- 상태 변경 (Go → Success 등)
- 발사 시각 변경 — 바뀌기 전이나 후에 시각이 확정(minute/day)인 건만.
  LL2가 미정(tbd) 일정의 임시 날짜를 옮기는 건 소음이라 뺀다.
이미지·설명 같은 나머지 필드만 바뀐 건은 알리지 않는다.
"""
from datetime import timedelta, timezone
from typing import Optional

from app.parser import parse_iso

KST = timezone(timedelta(hours=9))
CONFIRMED_PRECISIONS = ("minute", "day")
MAX_LINES = 10


def _escape(text: str) -> str:
    """Slack mrkdwn에서 특수하게 해석되는 문자 이스케이프."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_time(rec: dict) -> str:
    """발사 시각을 한국 시간으로. 정밀도에 따라 분까지 / 날짜만 / 미정."""
    t = parse_iso(rec.get("launch_time"))
    if t is None or rec.get("launch_time_precision") == "tbd":
        return "시각 미정"
    local = t.astimezone(KST)
    if rec.get("launch_time_precision") == "day":
        return f"{local.month}/{local.day}"
    return f"{local.month}/{local.day} {local:%H:%M} KST"


def _time_changed(before: dict, after: dict) -> bool:
    if (before["launch_time"], before["launch_time_precision"]) == (
        after["launch_time"], after["launch_time_precision"]
    ):
        return False
    return (
        before["launch_time_precision"] in CONFIRMED_PRECISIONS
        or after["launch_time_precision"] in CONFIRMED_PRECISIONS
    )


def summarize(changes: list[tuple[Optional[dict], dict]]) -> Optional[str]:
    """알릴 변경이 있으면 Slack 메시지 본문(여러 줄), 없으면 None."""
    added: list[str] = []
    changed: list[str] = []
    for before, after in changes:
        name = _escape(after["name"])
        if before is None:
            added.append(f"• 🆕 {name} — {format_time(after)}")
            continue
        notes = []
        if before["status"] != after["status"]:
            notes.append(f"{before['status']} → {after['status']}")
        if _time_changed(before, after):
            notes.append(f"{format_time(before)} → {format_time(after)}")
        if notes:
            changed.append(f"• {name}: {', '.join(notes)}")

    if not added and not changed:
        return None

    counts = " · ".join(
        part for part in (
            f"새 발사 {len(added)}건" if added else "",
            f"변경 {len(changed)}건" if changed else "",
        ) if part
    )
    lines = changed + added  # 상태·시각 변경이 더 급한 소식이라 먼저
    shown = lines[:MAX_LINES]
    if len(lines) > MAX_LINES:
        shown.append(f"…외 {len(lines) - MAX_LINES}건")
    return "\n".join([counts] + shown)
