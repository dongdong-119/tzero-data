"""발사 아카이브(JSON 파일) read/write. HTTP는 모름.

MongoDB 컬렉션을 대신하는 파일 2개:
- state/launches.json : 전체 발사 {launch_id: 레코드}. 지난 발사도 지우지 않는다.
- state/sweep.json    : {launch_id: 피드에서 안 보이기 시작한 시각}. 예전의
                        updated_at 역할 — 레코드에 매번 시각을 찍으면 매시간 diff가
                        생기므로, 피드에서 사라진 건만 따로 적어 둔다.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Union

from app.config import ARCHIVE_PATH, STUCK_AFTER_HOURS, SWEEP_PATH, TERMINAL_STATUSES
from app.models import ParsedLaunch
from app.parser import iso_z, parse_iso, to_record


def write_json(path: Path, obj: Union[dict, list]) -> None:
    """최상위 항목을 한 줄에 하나씩 쓴다 — 용량은 작게, git diff는 항목 단위로."""
    def dump(v) -> str:
        return json.dumps(v, ensure_ascii=False, separators=(",", ":"))

    if isinstance(obj, dict):
        body = ",\n".join(f"{dump(k)}:{dump(v)}" for k, v in obj.items())
        text = "{\n" + body + "\n}\n" if obj else "{}\n"
    else:
        body = ",\n".join(dump(v) for v in obj)
        text = "[\n" + body + "\n]\n" if obj else "[]\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


class Archive:
    def __init__(
        self,
        archive_path: Path = ARCHIVE_PATH,
        sweep_path: Path = SWEEP_PATH,
    ) -> None:
        self.archive_path = archive_path
        self.sweep_path = sweep_path
        self.records: dict[str, dict] = _read_json(archive_path)
        self.missing_since: dict[str, str] = _read_json(sweep_path)

    def save(self) -> None:
        """launch_id 순으로 정렬해 저장 — 레코드 위치가 고정돼 diff가 최소화된다."""
        write_json(self.archive_path, dict(sorted(self.records.items())))
        write_json(self.sweep_path, dict(sorted(self.missing_since.items())))

    def upsert(self, launches: list[ParsedLaunch]) -> dict[str, int]:
        """launch_id 기준 upsert. {saved, updated} 반환."""
        saved, updated = 0, 0
        for l in launches:
            rec = to_record(l)
            if rec["launch_id"] in self.records:
                updated += 1
            else:
                saved += 1
            self.records[rec["launch_id"]] = rec
        return {"saved": saved, "updated": updated}

    def mark_seen(self, seen: set[str], now: datetime) -> None:
        """이번 실행의 피드에 잡힌 launch_id 집합으로 '사라진 건' 장부를 갱신.

        모든 피드 호출이 성공한 실행에서만 불러야 한다 — 일부가 실패했다면
        그 발사사 건이 전부 '사라진 것'으로 잘못 기록된다.
        """
        for launch_id, rec in self.records.items():
            if rec["status"] in TERMINAL_STATUSES or launch_id in seen:
                self.missing_since.pop(launch_id, None)
            else:
                self.missing_since.setdefault(launch_id, iso_z(now))
        for launch_id in [i for i in self.missing_since if i not in self.records]:
            del self.missing_since[launch_id]

    def find_stuck(self, cap: int, now: datetime) -> list[dict]:
        """피드에서 사라진 지 STUCK_AFTER_HOURS가 넘은 비종결 레코드 (오래된 순, cap건).

        발사됐거나 취소되어 피드에서 빠진 건 — launch_id 단건 재조회로
        최종 상태를 받아와야 한다.
        """
        cutoff = now - timedelta(hours=STUCK_AFTER_HOURS)
        stuck = sorted(
            (since, launch_id)
            for launch_id, since in self.missing_since.items()
            if parse_iso(since) < cutoff
        )
        return [self.records[launch_id] for _, launch_id in stuck[:cap]]

    def touch(self, launch_id: str, now: datetime) -> None:
        """재조회 시각을 지금으로 갱신.

        재조회한 건(LL2에서 삭제된 404 포함)이 매 실행 콜을 소모하지 않도록
        STUCK_AFTER_HOURS 주기로만 다시 시도되게 미룬다.
        """
        if launch_id in self.missing_since:
            self.missing_since[launch_id] = iso_z(now)
