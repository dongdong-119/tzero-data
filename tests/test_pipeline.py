"""네트워크 없이 도는 파이프라인 테스트 (LL2 응답 모양의 가짜 데이터 사용).

실행: python3 -m unittest discover -s tests -v
"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.parser import parse_launch, to_record  # noqa: E402
from app.publish import PUBLIC_FIELDS, build_views, write_views  # noqa: E402
from app.report import summarize  # noqa: E402
from app.store import Archive  # noqa: E402

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


def ll2_item(launch_id, net, status="Go for Launch", tbddate=False, tbdtime=False,
             name="Falcon 9 Block 5 | Starlink Group 1-1"):
    """LL2 /launch/ 응답 1건의 최소 모양."""
    return {
        "id": launch_id,
        "name": name,
        "url": f"https://ll.thespacedevs.com/2.0.0/launch/{launch_id}/",
        "net": net,
        "tbddate": tbddate,
        "tbdtime": tbdtime,
        "status": {"name": status},
        "launch_service_provider": {"name": "SpaceX"},
        "rocket": {"configuration": {"full_name": "Falcon 9 Block 5", "family": "Falcon"}},
        "mission": {"type": "Communications", "orbit": {"name": "Low Earth Orbit"}},
        "pad": {"latitude": "28.56", "longitude": "-80.57",
                "location": {"name": "Cape Canaveral, FL, USA", "country_code": "USA"}},
        "image": "https://example.com/f9.jpg",
    }


def launch(*args, **kwargs):
    return parse_launch(ll2_item(*args, **kwargs))


class TempArchive(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.archive_path = self.tmp / "state" / "launches.json"
        self.sweep_path = self.tmp / "state" / "sweep.json"

    def archive(self) -> Archive:
        return Archive(self.archive_path, self.sweep_path)


class RecordTest(unittest.TestCase):
    def test_record_fields(self):
        rec = to_record(launch("a", "2026-11-15T03:21:00Z"))
        self.assertEqual(rec["launch_time"], "2026-11-15T03:21:00Z")
        self.assertEqual(rec["launch_time_precision"], "minute")
        self.assertEqual(rec["mission_name"], "Starlink Group 1-1")
        self.assertEqual(rec["rocket_family"], "Falcon")
        self.assertEqual(rec["country"], "USA")
        self.assertEqual(rec["latitude"], 28.56)
        self.assertNotIn("raw", rec)
        self.assertNotIn("updated_at", rec)

    def test_precision_and_missing_time(self):
        self.assertEqual(to_record(launch("a", "2026-12-31T00:00:00Z", tbdtime=True))
                         ["launch_time_precision"], "day")
        self.assertEqual(to_record(launch("a", "2026-12-31T00:00:00Z", tbddate=True, tbdtime=True))
                         ["launch_time_precision"], "tbd")
        self.assertIsNone(to_record(launch("a", None))["launch_time"])


class StoreTest(TempArchive):
    def test_upsert_counts_and_roundtrip(self):
        a = self.archive()
        self.assertEqual(a.upsert([launch("b", "2026-11-15T00:00:00Z"),
                                   launch("a", "2026-11-16T00:00:00Z")]),
                         {"saved": 2, "updated": 0})
        self.assertEqual(a.upsert([launch("a", "2026-11-17T00:00:00Z", status="Success")]),
                         {"saved": 0, "updated": 1})
        a.save()

        reloaded = self.archive()
        self.assertEqual(reloaded.records, a.records)
        self.assertEqual(reloaded.records["a"]["status"], "Success")
        self.assertEqual(list(json.loads(self.archive_path.read_text())), ["a", "b"])

    def test_resave_without_changes_is_byte_identical(self):
        a = self.archive()
        a.upsert([launch("a", "2026-11-15T00:00:00Z"), launch("b", None)])
        a.mark_seen({"a"}, NOW)
        a.save()
        before = (self.archive_path.read_bytes(), self.sweep_path.read_bytes())

        b = self.archive()
        b.upsert([launch("a", "2026-11-15T00:00:00Z"), launch("b", None)])
        b.mark_seen({"a"}, NOW + timedelta(hours=1))
        b.save()
        self.assertEqual(before, (self.archive_path.read_bytes(), self.sweep_path.read_bytes()))


class SweepTest(TempArchive):
    def test_missing_launch_becomes_stuck_after_48h(self):
        a = self.archive()
        a.upsert([launch("seen", "2026-11-20T00:00:00Z"),
                  launch("gone", "2026-11-10T00:00:00Z"),
                  launch("done", "2026-11-01T00:00:00Z", status="Success")])

        a.mark_seen({"seen"}, NOW)
        self.assertEqual(set(a.missing_since), {"gone"})  # 종결 건·피드에 잡힌 건은 제외
        self.assertEqual(a.find_stuck(cap=3, now=NOW + timedelta(hours=47)), [])
        stuck = a.find_stuck(cap=3, now=NOW + timedelta(hours=49))
        self.assertEqual([r["launch_id"] for r in stuck], ["gone"])

    def test_touch_defers_and_reappearing_clears(self):
        a = self.archive()
        a.upsert([launch("gone", "2026-11-10T00:00:00Z")])
        a.mark_seen(set(), NOW)

        later = NOW + timedelta(hours=49)
        a.touch("gone", later)  # 재조회했지만 아직 비종결(또는 404) → 48시간 뒤로 미룸
        self.assertEqual(a.find_stuck(cap=3, now=later + timedelta(hours=1)), [])
        self.assertEqual(len(a.find_stuck(cap=3, now=later + timedelta(hours=49))), 1)

        a.mark_seen({"gone"}, later)  # 피드에 다시 나타남
        self.assertEqual(a.missing_since, {})

    def test_stuck_is_oldest_first_and_capped(self):
        a = self.archive()
        a.upsert([launch("x", "2026-11-10T00:00:00Z")])
        a.mark_seen(set(), NOW)
        a.upsert([launch("y", "2026-11-11T00:00:00Z"), launch("z", "2026-11-12T00:00:00Z")])
        a.mark_seen(set(), NOW + timedelta(hours=1))

        stuck = a.find_stuck(cap=2, now=NOW + timedelta(hours=100))
        self.assertEqual([r["launch_id"] for r in stuck], ["x", "y"])


class ReportTest(TempArchive):
    """Slack 알림용 변경 요약."""

    def run_changes(self, first, second):
        a = self.archive()
        a.upsert(first)
        a.save()
        b = self.archive()  # 다음 실행
        b.upsert(second)
        return b.changes()

    def test_no_changes_means_no_message(self):
        same = [launch("a", "2026-11-15T03:21:00Z")]
        self.assertEqual(self.run_changes(same, same), [])
        self.assertIsNone(summarize([]))

    def test_status_time_and_new_launch(self):
        changes = self.run_changes(
            [launch("a", "2026-11-15T03:21:00Z", name="Falcon 9 | A"),
             launch("b", "2026-11-16T00:00:00Z", name="Electron | B")],
            [launch("a", "2026-11-15T03:21:00Z", name="Falcon 9 | A", status="Success"),
             launch("b", "2026-11-17T15:30:00Z", name="Electron | B"),
             launch("c", "2026-11-20T00:00:00Z", name="Vulcan | C <test>", tbdtime=True)],
        )
        self.assertEqual(summarize(changes).splitlines(), [
            "새 발사 1건 · 변경 2건",
            "• Falcon 9 | A: Go for Launch → Success",
            "• Electron | B: 11/16 09:00 KST → 11/18 00:30 KST",  # UTC+9
            "• 🆕 Vulcan | C &lt;test&gt; — 11/20",                # 날짜만 확정
        ])

    def test_placeholder_shuffles_and_minor_fields_are_ignored(self):
        before = [launch("a", "2026-12-31T00:00:00Z", tbddate=True, tbdtime=True),
                  launch("b", "2026-11-16T00:00:00Z")]
        moved = ll2_item("b", "2026-11-16T00:00:00Z")
        moved["image"] = "https://example.com/new.jpg"
        after = [launch("a", "2027-03-31T00:00:00Z", tbddate=True, tbdtime=True),
                 parse_launch(moved)]
        changes = self.run_changes(before, after)
        self.assertEqual(len(changes), 2)       # 아카이브에는 둘 다 반영되지만
        self.assertIsNone(summarize(changes))   # 알릴 만한 변경은 아님

    def test_same_launch_in_two_feeds_is_compared_once(self):
        a = self.archive()
        a.upsert([launch("a", "2026-11-15T03:21:00Z")])
        a.save()
        b = self.archive()
        b.upsert([launch("a", "2026-11-15T03:21:00Z", status="In Flight")])  # upcoming 피드
        b.upsert([launch("a", "2026-11-15T03:21:00Z", status="Success")])    # previous 피드
        self.assertEqual(summarize(b.changes()).splitlines()[1],
                         "• Falcon 9 Block 5 | Starlink Group 1-1: Go for Launch → Success")

    def test_long_list_is_capped(self):
        many = [launch(f"id{i:02d}", "2026-11-15T03:21:00Z", name=f"R | M{i}") for i in range(14)]
        lines = summarize(self.run_changes([], many)).splitlines()
        self.assertEqual(lines[0], "새 발사 14건")
        self.assertEqual(len(lines), 12)  # 제목 + 10줄 + "…외 4건"
        self.assertEqual(lines[-1], "…외 4건")


class BackfillTest(unittest.TestCase):
    """LL2는 날짜 필터를 무시하므로, 페이지를 넘기다 since 이전에서 멈춰야 한다."""

    def fetch(self, pages, since):
        from unittest import mock
        from app import ll2

        calls = []

        def fake_get(url):
            calls.append(url)
            return pages[len(calls) - 1]

        with mock.patch.object(ll2, "_get", fake_get):
            launches = ll2.fetch_previous_since(121, since)
        return [l.raw["id"] for l in launches], len(calls)

    def test_stops_at_cutoff_and_drops_older(self):
        pages = [
            {"next": "page2", "results": [ll2_item("n1", "2026-11-01T00:00:00Z"),
                                          ll2_item("n2", "2026-08-01T00:00:00Z")]},
            {"next": "page3", "results": [ll2_item("n3", "2026-06-02T00:00:00Z"),
                                          ll2_item("old", "2026-05-30T00:00:00Z")]},
            {"next": None, "results": [ll2_item("older", "1989-04-02T00:00:00Z")]},
        ]
        ids, calls = self.fetch(pages, datetime(2026, 6, 1, tzinfo=timezone.utc))
        self.assertEqual(ids, ["n1", "n2", "n3"])
        self.assertEqual(calls, 2)  # 3번째 페이지는 요청하지 않는다

    def test_stops_on_last_page(self):
        pages = [{"next": None, "results": [ll2_item("n1", "2026-11-01T00:00:00Z")]}]
        ids, calls = self.fetch(pages, datetime(2026, 6, 1, tzinfo=timezone.utc))
        self.assertEqual((ids, calls), (["n1"], 1))


class PublishTest(TempArchive):
    def records(self):
        a = self.archive()
        a.upsert([
            launch("past", "2026-10-30T23:30:00Z", status="Success"),
            launch("soon", "2026-11-14T13:00:00Z"),
            launch("later", "2026-12-02T00:00:00Z", tbdtime=True),
            launch("placeholder", "2026-12-31T00:00:00Z", tbddate=True, tbdtime=True),
            launch("undated", None),
        ])
        return a.records.values()

    def test_upcoming_is_future_only_sorted_and_public(self):
        upcoming = build_views(self.records(), NOW)["upcoming.json"]
        self.assertEqual([r["launch_id"] for r in upcoming], ["soon", "later", "placeholder"])
        self.assertEqual(tuple(upcoming[0]), PUBLIC_FIELDS)

    def test_months_exclude_placeholders_and_keep_past(self):
        views = build_views(self.records(), NOW)
        self.assertEqual(views["index.json"], {"months": ["2026-10", "2026-11", "2026-12"]})
        self.assertEqual([r["launch_id"] for r in views["months/2026-10.json"]], ["past"])
        self.assertEqual([r["launch_id"] for r in views["months/2026-12.json"]], ["later"])

    def test_write_views_removes_stale_month_files(self):
        out = self.tmp / "data"
        write_views(build_views(self.records(), NOW), out)
        stale = out / "months" / "2025-01.json"
        stale.write_text("[]\n")

        write_views(build_views(self.records(), NOW), out)
        self.assertFalse(stale.exists())
        self.assertEqual(sorted(p.name for p in (out / "months").iterdir()),
                         ["2026-10.json", "2026-11.json", "2026-12.json"])
        self.assertEqual(len(json.loads((out / "upcoming.json").read_text())), 3)


if __name__ == "__main__":
    unittest.main()
