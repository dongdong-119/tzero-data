"""CLI: 설정된 발사사들의 발사 데이터를 LL2에서 받아 JSON 파일로 저장·공개.

지난 발사는 삭제하지 않는다(캘린더/Likes 과거 조회용) — upcoming 피드에서
사라진 건은 previous 피드·단건 재조회로 최종 상태와 실제 발사 시각만 반영한다.
콜 예산: upcoming 4 + previous 1 + 재조회 ≤3 = 회당 ≤8콜 (LL2 리밋 15/시간).

LL2 호출이 실패해도(레이트리밋 등) 기존 데이터는 그대로 두고, 시간이 지나
upcoming에서 빠져야 할 건만 정리해서 다시 쓴다.

실행:
  python3 ingest.py                            # 일반 수집
  python3 ingest.py --backfill-from 2026-06-01 # 과거 발사 백필(발사사당 1~4콜) 후 일반 수집
"""
import argparse
import os
import uuid
from datetime import datetime, timezone

from app.config import PREVIOUS_FETCH_LIMIT, PROVIDER_LSP_IDS, STUCK_SWEEP_CAP, TERMINAL_STATUSES
from app.ll2 import (
    LL2Error,
    LL2RateLimited,
    fetch_by_id,
    fetch_previous,
    fetch_previous_since,
    fetch_upcoming,
)
from app.publish import build_views, write_views
from app.report import summarize
from app.store import Archive

FAILED = object()  # LL2Calls.call()의 실패 표시 (fetch_by_id의 정상 None=404와 구분)


class LL2Calls:
    """LL2 호출 래퍼 — 실패는 경고만 남기고 넘어가며 성공/실패 횟수를 센다."""

    def __init__(self) -> None:
        self.ok = 0
        self.failed = 0
        self.rate_limited = False

    def call(self, label: str, fn, *args, **kwargs):
        if self.rate_limited:  # 한도 소진 — 남은 호출도 어차피 429
            self.failed += 1
            return FAILED
        try:
            result = fn(*args, **kwargs)
        except LL2RateLimited as e:
            self.rate_limited = True
            self.failed += 1
            print(f"::warning::[{label}] {e} — 이번 실행의 남은 LL2 호출은 건너뜀")
            return FAILED
        except LL2Error as e:
            self.failed += 1
            print(f"::warning::[{label}] LL2 호출 실패: {e}")
            return FAILED
        self.ok += 1
        return result


def _save(archive: Archive, label: str, launches: list, seen: set) -> None:
    result = archive.upsert(launches)
    seen.update(l.raw["id"] for l in launches)
    print(
        f"[{label:24s}] 가져옴 {len(launches):3d}건"
        f" → 신규 {result['saved']:3d}, 갱신 {result['updated']:3d}"
    )


def backfill(archive: Archive, ll2: LL2Calls, since: datetime) -> None:
    """발사사별로 since 이후 완료된 발사를 채운다 (최초 1회용, 발사사당 1~4콜)."""
    for label, lsp_id in PROVIDER_LSP_IDS.items():
        launches = ll2.call(label, fetch_previous_since, lsp_id, since)
        if launches is not FAILED:
            _save(archive, f"Backfill {label}", launches, set())


def ingest_upcoming(archive: Archive, ll2: LL2Calls, seen: set) -> None:
    """발사사별 다가오는 발사 수집 (발사사당 1콜)."""
    for label, lsp_id in PROVIDER_LSP_IDS.items():
        launches = ll2.call(label, fetch_upcoming, lsp_id=lsp_id, limit=50)
        if launches is not FAILED:
            _save(archive, label, launches, seen)


def ingest_previous(archive: Archive, ll2: LL2Calls, seen: set) -> None:
    """최근 완료된 발사의 최종 상태 반영 (전체 1콜, 대상 발사사만 저장)."""
    launches = ll2.call("Previous", fetch_previous, limit=PREVIOUS_FETCH_LIMIT)
    if launches is not FAILED:
        ours = set(PROVIDER_LSP_IDS)
        _save(archive, "Previous", [l for l in launches if l.provider in ours], seen)


def sweep_stuck(archive: Archive, ll2: LL2Calls, now: datetime) -> None:
    """피드에서 사라진 비종결 건을 단건 재조회 (실행당 ≤ STUCK_SWEEP_CAP콜).

    previous 피드 범위(최근 50건) 밖으로 밀려난 옛 발사나 취소 건을 회수한다.
    """
    for rec in archive.find_stuck(cap=STUCK_SWEEP_CAP, now=now):
        launch = ll2.call("Sweep", fetch_by_id, rec["launch_id"])
        if launch is FAILED:
            continue
        if launch is None:  # LL2에서 삭제됨 — 다음 주기로 미룸
            archive.touch(rec["launch_id"], now)
            print(f"[{'Sweep':24s}] {rec['name']} → LL2에 없음(404), 보류")
            continue
        archive.upsert([launch])
        if launch.status in TERMINAL_STATUSES:
            archive.missing_since.pop(rec["launch_id"], None)
        else:
            archive.touch(rec["launch_id"], now)
        print(f"[{'Sweep':24s}] {rec['name']} → {launch.status}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--backfill-from",
        metavar="YYYY-MM-DD",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc),
        help="이 날짜(UTC) 이후 완료된 발사를 먼저 채운다",
    )
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    archive = Archive()
    ll2 = LL2Calls()

    if args.backfill_from:
        backfill(archive, ll2, args.backfill_from)

    failed_before_feeds = ll2.failed
    seen: set = set()
    ingest_upcoming(archive, ll2, seen)
    ingest_previous(archive, ll2, seen)
    if ll2.failed == failed_before_feeds:
        archive.mark_seen(seen, now)
        sweep_stuck(archive, ll2, now)
    else:
        print("피드 일부 실패 — 이번 실행은 '사라진 건' 점검을 건너뜀")

    archive.save()
    views = build_views(archive.records.values(), now)
    write_views(views)
    print(
        f"아카이브 {len(archive.records)}건 · upcoming {len(views['upcoming.json'])}건"
        f" · 월 파일 {len(views['index.json']['months'])}개"
        f" · LL2 호출 성공 {ll2.ok} / 실패 {ll2.failed}"
    )

    summary = summarize(archive.changes())
    print(f"── 알릴 변경 ──\n{summary}" if summary else "알릴 변경 없음")

    # GitHub Actions: 워크플로우의 ll2-health · notify 잡이 쓰는 값들
    if os.environ.get("GITHUB_OUTPUT"):
        text = summary or (
            f"변경 없음 · 보관 {len(archive.records)}건"
            f" · 예정 {len(views['upcoming.json'])}건"
        )
        delim = f"EOF_{uuid.uuid4().hex}"  # 여러 줄 출력용 구분자 (본문과 안 겹치게 임의 값)
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"ll2_ok={'true' if ll2.ok > 0 else 'false'}\n")
            f.write(f"notify={'true' if summary else 'false'}\n")
            f.write(f"slack_text<<{delim}\n{text}\n{delim}\n")
    elif ll2.ok == 0:
        print("LL2 호출이 전부 실패했어요 — 기존 데이터는 유지됩니다.")


if __name__ == "__main__":
    main()
