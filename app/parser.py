"""LL2 응답 → 정규화. 순수 함수만 (I/O 없음, 테스트 쉬움)."""
from datetime import datetime, timezone
from typing import Optional

from app.models import ParsedLaunch


# ──────────────────────────────────────────────
# 변환 헬퍼
# ──────────────────────────────────────────────
def parse_iso(dt: Optional[str]) -> Optional[datetime]:
    if not dt:
        return None
    try:
        return datetime.fromisoformat(dt.replace("Z", "+00:00"))
    except ValueError:
        return None


def iso_z(dt: Optional[datetime]) -> Optional[str]:
    """datetime → 'YYYY-MM-DDTHH:MM:SSZ' (UTC). JSON에 저장하는 표준 형식."""
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def split_mission_name(name: str) -> Optional[str]:
    """LL2의 'Rocket | Mission' 형태에서 미션명만 추출."""
    if not name or " | " not in name:
        return None
    return name.split(" | ", 1)[1].strip()


def derive_time_precision(tbddate: bool, tbdtime: bool) -> str:
    """tbddate/tbdtime → launch_time 정밀도. minute / day / tbd."""
    if not tbddate and not tbdtime:
        return "minute"
    if not tbddate and tbdtime:
        return "day"
    return "tbd"


def derive_rocket_family(family: Optional[str], rocket: Optional[str]) -> Optional[str]:
    """LL2의 family가 비어있으면 rocket 이름 자체를 family로 사용."""
    if family:
        return family
    return rocket


# ──────────────────────────────────────────────
# LL2 응답 → ParsedLaunch
# ──────────────────────────────────────────────
def parse_launch(item: dict) -> ParsedLaunch:
    status   = item.get("status") or {}
    lsp      = item.get("launch_service_provider") or {}
    rocket   = (item.get("rocket") or {}).get("configuration") or {}
    mission  = item.get("mission") or {}
    orbit    = mission.get("orbit") or {}
    pad      = item.get("pad") or {}
    location = pad.get("location") or {}

    return ParsedLaunch(
        name=item.get("name", "Unknown"),
        provider=lsp.get("name", "Unknown"),
        country=location.get("country_code"),
        rocket=rocket.get("full_name") or rocket.get("name"),
        launch_time=parse_iso(item.get("net")),
        launch_site=location.get("name"),
        status=status.get("name", "Unknown"),
        source_url=item.get("url"),
        raw={
            "id": item["id"],
            "window_start": item.get("window_start"),
            "window_end": item.get("window_end"),
            "time_confirmed": not item.get("tbdtime", False),
            "tbddate": item.get("tbddate", False),
            "rocket_family": rocket.get("family"),
            "mission_type": mission.get("type"),
            "mission_description": mission.get("description"),
            "orbit": orbit.get("name"),
            "latitude": to_float(pad.get("latitude")),
            "longitude": to_float(pad.get("longitude")),
            "image": item.get("image"),
        },
    )


# ──────────────────────────────────────────────
# ParsedLaunch → 아카이브 레코드 (JSON에 그대로 저장되는 dict)
# ──────────────────────────────────────────────
def to_record(l: ParsedLaunch) -> dict:
    """LL2 원본(raw)과 updated_at은 저장하지 않는다.

    매시간 커밋되는 파일이라, 내용이 안 바뀌면 diff도 없어야 하기 때문.
    """
    return {
        "launch_id": l.raw["id"],          # UUID = 중복 판별 키
        "name": l.name,
        "mission_name": split_mission_name(l.name),
        "provider": l.provider,
        "country": l.country,
        "rocket": l.rocket,
        "rocket_family": derive_rocket_family(l.raw.get("rocket_family"), l.rocket),
        "launch_time": iso_z(l.launch_time),
        "launch_time_precision": derive_time_precision(
            l.raw.get("tbddate", False),
            not l.raw.get("time_confirmed", True),
        ),
        "launch_site": l.launch_site,
        "status": l.status,
        "source_url": l.source_url,
        "mission_type": l.raw.get("mission_type"),
        "mission_description": l.raw.get("mission_description"),
        "orbit": l.raw.get("orbit"),
        "latitude": l.raw.get("latitude"),
        "longitude": l.raw.get("longitude"),
        "image": l.raw.get("image"),
        "time_confirmed": l.raw.get("time_confirmed"),
        "window_start": iso_z(parse_iso(l.raw.get("window_start"))),
        "window_end": iso_z(parse_iso(l.raw.get("window_end"))),
    }
