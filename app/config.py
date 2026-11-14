"""환경 / 상수."""
from pathlib import Path
from typing import Final

LL2_BASE: Final[str] = "https://ll.thespacedevs.com/2.0.0"
HTTP_TIMEOUT_SEC: Final[int] = 30
USER_AGENT: Final[str] = "RocketParser/1.0"

# ── 파일 위치 ──────────────────────────────────────
# state/ = 내부 원본(전체 아카이브), data/ = 공개용 뷰(GitHub Pages로 그대로 배포)
ROOT: Final[Path] = Path(__file__).resolve().parent.parent
ARCHIVE_PATH: Final[Path] = ROOT / "state" / "launches.json"
SWEEP_PATH: Final[Path] = ROOT / "state" / "sweep.json"
DATA_DIR: Final[Path] = ROOT / "data"

# 수집 대상 발사사 (LSP ID는 ll.thespacedevs.com/2.0.0/agencies/?search= 로 조회)
PROVIDER_LSP_IDS: Final[dict[str, int]] = {
    "SpaceX": 121,
    "Rocket Lab": 147,
    "United Launch Alliance": 124,
    "Arianespace": 115,
}

# ── 지난 발사 아카이브 ─────────────────────────────
# 지난 발사는 삭제하지 않는다(캘린더/Likes에서 과거 조회용). upcoming 피드에서
# 사라진 건은 최종 상태(Success 등)와 실제 발사 시각으로 "수정"만 한다.
# 콜 예산: upcoming 4 + previous 1 + 재조회 최대 3 = 회당 최대 8콜 (LL2 리밋 15/시간)
PREVIOUS_FETCH_LIMIT: Final[int] = 50   # /launch/previous/ 1콜로 받는 최근 완료 발사 수(전체 발사사)
STUCK_SWEEP_CAP: Final[int] = 3         # 실행당 단건 재조회(fetch_by_id) 최대 콜 수
STUCK_AFTER_HOURS: Final[int] = 48      # 이 시간 넘게 피드에 안 보이면 단건 재조회 대상
TERMINAL_STATUSES: Final[tuple[str, ...]] = ("Success", "Failure", "Partial Failure")

# 백필(--backfill-from) 페이지 크기와 발사사당 최대 페이지 수(=최대 콜 수).
# detailed 응답이 커서 100건씩 받으면 타임아웃이 난다 — 일반 수집과 같은 50건으로.
BACKFILL_PAGE_SIZE: Final[int] = 50
BACKFILL_MAX_PAGES: Final[int] = 4
