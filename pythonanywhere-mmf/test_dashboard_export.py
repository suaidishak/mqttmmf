import copy
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from export_dashboard import assemble, read_dashboard, request_rows


class DashboardExportTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
        self.capture = {"site_code": "TEST-LSS", "captured_at": "2026-01-01T12:00:00Z", "station_count": 4, "rawPayload": "private"}
        self.row = {"site_code": "TEST-LSS", "captured_at": "2026-01-01T12:00:00Z", "parameter_code": "ghi", "average_value": 0,
                    "contributing_stations": 2, "unit": None, "source_units": [], "unit_conflict": False, "stations_with_unit": 0,
                    "rawPayload": "private", "secret": "must-never-export"}

    def build(self, row=None, capture=None):
        return assemble("TEST-LSS", [capture or self.capture], [row or self.row], self.now)

    def test_allowlist_excludes_raw_data_and_secrets(self):
        result = self.build()
        self.assertNotIn("private", str(result))
        self.assertNotIn("must-never-export", str(result))
        self.assertEqual(result["sites"][0]["captures"][0]["parameters"][0]["averageValue"], 0)

    def test_missing_unit_and_partial_station_coverage_are_preserved(self):
        p = self.build()["sites"][0]["captures"][0]["parameters"][0]
        self.assertEqual(p["contributingStations"], 2)
        self.assertIsNone(p["unit"])

    def test_unit_conflicts_preserve_numeric_mean(self):
        row = dict(self.row, source_units=["m/s", "W/m²"], unit_conflict=True, stations_with_unit=2)
        p = self.build(row)["sites"][0]["captures"][0]["parameters"][0]
        self.assertTrue(p["unitConflict"])
        self.assertEqual(p["averageValue"], 0)

    def test_scope_blocks_other_sites(self):
        with self.assertRaises(ValueError):
            self.build(dict(self.row, site_code="OTHER"))

    def test_unknown_capture_is_rejected(self):
        with self.assertRaises(ValueError):
            self.build(dict(self.row, captured_at="2026-01-01T11:00:00Z"))

    def test_nonfinite_values_and_booleans_rejected(self):
        for value in (float("nan"), float("inf"), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.build(dict(self.row, average_value=value))

    def test_invalid_contributor_counts_rejected(self):
        for count in (0, 5, True):
            with self.subTest(count=count), self.assertRaises(ValueError):
                self.build(dict(self.row, contributing_stations=count))

    def test_duplicates_and_incomplete_captures_rejected(self):
        with self.assertRaises(ValueError):
            assemble("TEST-LSS", [self.capture], [self.row, self.row], self.now)
        with self.assertRaises(ValueError):
            assemble("TEST-LSS", [self.capture], [], self.now)

    def test_empty_database_is_valid(self):
        self.assertEqual(assemble("TEST-LSS", [], [], self.now)["sites"][0]["captures"], [])

    def test_utc_normalization_and_same_second_ordering(self):
        second = dict(self.capture, captured_at="2026-01-01T12:00:00.100000Z")
        row2 = dict(self.row, captured_at=second["captured_at"])
        result = assemble("TEST-LSS", [second, self.capture], [row2, self.row], self.now)
        self.assertLess(result["sites"][0]["captures"][0]["capturedAt"], result["sites"][0]["captures"][1]["capturedAt"])
        shifted = dict(self.capture, captured_at="2026-01-01T20:00:00+08:00")
        self.assertEqual(self.build(capture=shifted)["sites"][0]["captures"][0]["capturedAt"], "2026-01-01T12:00:00.000000Z")

    def test_keyset_pagination_continues_under_smaller_rest_cap(self):
        extra = dict(self.row, parameter_code="new_param")
        with patch("export_dashboard.request_rows", side_effect=[[self.capture], [self.row], [extra], []]) as query:
            data = read_dashboard(None, "https://example.supabase.co", "unused", "TEST-LSS", self.now)
        self.assertEqual(len(data["sites"][0]["captures"][0]["parameters"]), 2)
        self.assertIn(("or", "(captured_at.lt.2026-01-01T12:00:00.000000Z,and(captured_at.eq.2026-01-01T12:00:00.000000Z,parameter_code.gt.ghi))"), query.call_args_list[2].args[-1])

    def test_query_failure_is_generic_and_closes_response(self):
        class Response:
            status_code = 403
            content = b"private server details"
            closed = False
            def close(self): self.closed = True
        response = Response()
        class Session:
            def get(self, *args, **kwargs): return response
        with self.assertRaisesRegex(RuntimeError, "^Dashboard query failed$"):
            request_rows(Session(), "https://example.supabase.co", "not-real", "table", [])
        self.assertTrue(response.closed)


if __name__ == "__main__":
    unittest.main()
