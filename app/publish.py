"""아카이브 → 공개용 JSON 뷰 (data/). 예전 FastAPI 엔드포인트를 대신한다.

- data/upcoming.json        ← GET /launches/upcoming  (생성 시각 이후 발사, 가까운 순)
- data/months/YYYY-MM.json  ← GET /launches/range     (UTC 기준 월별, 과거 포함, 시간순)
- data/index.json           ← 존재하는 월 목록 {"months": [...]}

클라이언트 참고:
- 파일은 최대 1시간(+지연) 묵은 것일 수 있으므로 upcoming은 클라이언트에서도
  launch_time >= now 로 한 번 더 거른다.
- 월 파일은 UTC 기준이라, 현지 날짜(KST 등)로 캘린더를 그리려면 보는 달의
  앞뒤 달 파일도 같이 받아서 현지 날짜로 다시 묶는다.
"""
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable, Union

from app.config import DATA_DIR
from app.parser import parse_iso
from app.store import write_json

# 클라이언트 응답 필드 — 예전 API(UpcomingLaunch)와 동일. 내부 필드는 제외.
PUBLIC_FIELDS = (
    "launch_id", "name", "mission_name", "provider", "country",
    "rocket", "rocket_family", "launch_time", "launch_time_precision",
    "launch_site", "status", "mission_type", "orbit", "image", "source_url",
)

# 시각이 확정된 정밀도. LL2가 임의로 찍는 월말/연말 placeholder(tbd)는
# 캘린더에 실제 일정처럼 보이므로 월 파일에서 제외한다.
CONFIRMED_PRECISIONS = ("minute", "day")


def _public(rec: dict) -> dict:
    return {f: rec.get(f) for f in PUBLIC_FIELDS}


def build_views(records: Iterable[dict], now: datetime) -> dict[str, Union[list, dict]]:
    """{data/ 기준 상대 경로: 내용}. launch_time이 없는 건은 어느 뷰에도 안 나간다."""
    dated = sorted(
        ((parse_iso(r["launch_time"]), r) for r in records if r.get("launch_time")),
        key=lambda pair: (pair[0], pair[1]["launch_id"]),
    )

    months: dict[str, list] = defaultdict(list)
    for t, rec in dated:
        if rec["launch_time_precision"] in CONFIRMED_PRECISIONS:
            months[t.strftime("%Y-%m")].append(_public(rec))

    views: dict[str, Union[list, dict]] = {
        "upcoming.json": [_public(rec) for t, rec in dated if t >= now],
        "index.json": {"months": sorted(months)},
    }
    for month, items in months.items():
        views[f"months/{month}.json"] = items
    return views


def write_views(views: dict[str, Union[list, dict]], out_dir: Path = DATA_DIR) -> None:
    for rel, content in views.items():
        write_json(out_dir / rel, content)
    # 발사가 다른 달로 옮겨져 비게 된 월 파일 정리
    for stale in (out_dir / "months").glob("*.json"):
        if f"months/{stale.name}" not in views:
            stale.unlink()
