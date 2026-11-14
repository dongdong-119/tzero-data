"""Launch Library 2 HTTP 클라이언트. 저장소(파일)는 모름.

표준 라이브러리만 사용 — GitHub Actions에서 pip install 없이 바로 돈다.
"""
import json
from datetime import datetime
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.config import (
    BACKFILL_MAX_PAGES,
    BACKFILL_PAGE_SIZE,
    HTTP_TIMEOUT_SEC,
    LL2_BASE,
    USER_AGENT,
)
from app.models import ParsedLaunch
from app.parser import parse_launch


class LL2Error(Exception):
    """LL2 호출 실패 (네트워크·서버 오류·응답 파싱 실패)."""


class LL2RateLimited(LL2Error):
    """HTTP 429 — 이 IP의 시간당 호출 한도 소진. 같은 실행에서 재시도해도 소용없다."""


def _get(url: str) -> Optional[dict]:
    """GET → JSON. 404면 None."""
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
            return json.load(resp)
    except HTTPError as e:
        if e.code == 404:
            return None
        if e.code == 429:
            raise LL2RateLimited("HTTP 429 (rate limited)") from e
        raise LL2Error(f"HTTP {e.code}") from e
    except (URLError, TimeoutError, ValueError) as e:
        raise LL2Error(f"{type(e).__name__}: {e}") from e


def _list(path: str, params: dict) -> dict:
    data = _get(f"{LL2_BASE}{path}?{urlencode(params)}")
    if data is None:
        raise LL2Error(f"HTTP 404 ({path})")
    return data


def fetch_upcoming(lsp_id: Optional[int] = None, limit: int = 100) -> list[ParsedLaunch]:
    """다가오는 발사를 LL2에서 받아 ParsedLaunch 리스트로 반환.

    lsp_id를 지정하지 않으면 전체 발사사 대상.
    """
    params: dict = {"limit": limit, "mode": "detailed"}
    if lsp_id is not None:
        params["lsp__id"] = lsp_id
    data = _list("/launch/upcoming/", params)
    return [parse_launch(item) for item in data.get("results", [])]


def fetch_previous(limit: int = 50) -> list[ParsedLaunch]:
    """최근 완료된 발사를 LL2에서 받아 반환 (전체 발사사, 최신순).

    upcoming 피드에서 사라진 발사의 최종 상태·실제 발사 시각을 반영하는 용도.
    발사사 필터는 호출부에서 provider명으로 거른다 (1콜로 전체를 받는 게 저렴).
    """
    data = _list("/launch/previous/", {"limit": limit, "mode": "detailed"})
    return [parse_launch(item) for item in data.get("results", [])]


def fetch_previous_since(lsp_id: int, since: datetime) -> list[ParsedLaunch]:
    """한 발사사의 since 이후 완료 발사 (백필용, 페이지당 1콜).

    LL2 2.0.0은 날짜 필터(net__gte 등)를 조용히 무시한다. 그래서 최신순으로
    페이지를 넘기다가 since보다 오래된 발사가 나오면 멈추고, 오래된 건은 버린다.
    BACKFILL_MAX_PAGES에 먼저 닿으면 거기까지만 반환한다.
    """
    url: Optional[str] = f"{LL2_BASE}/launch/previous/?" + urlencode({
        "lsp__id": lsp_id,
        "limit": BACKFILL_PAGE_SIZE,
        "mode": "detailed",
    })
    launches: list[ParsedLaunch] = []
    for _ in range(BACKFILL_MAX_PAGES):
        data = _get(url)
        if data is None:
            raise LL2Error("HTTP 404 (/launch/previous/)")
        page = [parse_launch(item) for item in data.get("results", [])]
        fresh = [l for l in page if l.launch_time and l.launch_time >= since]
        launches += fresh
        url = data.get("next")
        if len(fresh) < len(page) or not url:  # since 이전에 닿았거나 마지막 페이지
            break
    return launches


def fetch_by_id(launch_id: str) -> Optional[ParsedLaunch]:
    """launch_id 단건 조회. LL2에서 삭제된 경우(404) None."""
    data = _get(f"{LL2_BASE}/launch/{launch_id}/?" + urlencode({"mode": "detailed"}))
    return parse_launch(data) if data is not None else None
