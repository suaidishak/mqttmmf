import math
import os
import unittest
from unittest.mock import Mock, patch

from averages import average_snapshot
from collector import Readings, collect_once
from supabase_writer import write_averages


class AverageTests(unittest.TestCase):
    def test_different_parameter_sets_correct_denominators(self):
        readings = Readings("TEST", "SYNTHETIC")
        readings.set_connected(True)
        readings.accept("TEST/MMF", b'{"MMF1":{"mw":0,"ghi":100},"MMF2":{"mw":4,"ghi":200,"temp":30},"MMF3":{"mw":8,"temp":34},"MMF4":{"mw":12}}', received_at=900)
        summary = average_snapshot(readings.snapshot(901))
        parameters = {p["parameterCode"]: p for p in summary["parameters"]}
        self.assertEqual(parameters["mw"]["averageValue"], 6)
        self.assertEqual(parameters["mw"]["contributingStations"], 4)
        self.assertEqual(parameters["ghi"]["averageValue"], 150)
        self.assertEqual(parameters["ghi"]["contributingStations"], 2)
        self.assertEqual(parameters["temp"]["averageValue"], 32)

    def test_latest_per_station_without_mirror_double_counting(self):
        readings = Readings("TEST", "SYNTHETIC")
        readings.accept("TEST/MMF", b'{"MMF1":{"mw":2},"MMF2":{"mw":8}}', received_at=900)
        readings.accept("TEST/MMF1/mw", b'{"value":4,"unit":"MW","timestamp":"source-time","type":"MeasureValue"}', received_at=900)
        readings.accept("TEST/MMF1/mw", b'{"value":6,"unit":"MW"}', received_at=900.5)
        summary = average_snapshot(readings.snapshot(901))
        self.assertEqual(summary["parameters"][0]["averageValue"], 7)
        self.assertEqual(summary["parameters"][0]["contributingStations"], 2)
        self.assertEqual(summary["parameters"][0]["stationsWithUnit"], 1)

    def test_metadata_null_text_boolean_and_stale_are_excluded(self):
        readings = Readings("TEST", "SYNTHETIC")
        readings.accept("TEST/MMF1", b'{"ghi":10,"timestamp":"not-a-parameter","unit":"MW","quality":3,"bad":null,"flag":true,"text":"12"}', received_at=900)
        readings.accept("TEST/MMF2/ghi", b"1000", retained=True, received_at=900)
        readings.accept("TEST/MMF3/ghi", b"1000", received_at=0)
        summary = average_snapshot(readings.snapshot(901))
        self.assertEqual(len(summary["parameters"]), 1)
        self.assertEqual(summary["parameters"][0]["averageValue"], 10)
        self.assertEqual(summary["parameters"][0]["contributingStations"], 1)
        self.assertEqual(summary["unverifiedMessages"], 2)

    def test_labels_are_metadata_and_conflicts_do_not_exclude_numbers(self):
        readings = Readings("TEST", "SYNTHETIC")
        readings.accept("TEST/MMF1/mw", b'{"value":1,"unit":"MW"}', received_at=900)
        readings.accept("TEST/MMF2/mw", b'{"value":1000,"unit":"kW"}', received_at=900)
        summary = average_snapshot(readings.snapshot(901))
        self.assertEqual(summary["parameters"][0]["averageValue"], 500.5)
        self.assertEqual(summary["parameters"][0]["contributingStations"], 2)
        self.assertEqual(summary["parameters"][0]["sourceUnits"], ["MW", "kW"])
        self.assertTrue(summary["parameters"][0]["unitConflict"])
        self.assertIsNone(summary["parameters"][0]["unit"])
        self.assertEqual(summary["unitConflicts"], ["mw"])
        readings.accept("TEST/MMF3/mw", b'{"value":2,"unit":42}', received_at=900)
        summary = average_snapshot(readings.snapshot(901))
        self.assertEqual(summary["parameters"][0]["contributingStations"], 3)
        self.assertEqual(summary["parameters"][0]["stationsWithUnit"], 2)

    def test_configurable_station_count_and_new_parameters(self):
        readings = Readings("TEST", "SYNTHETIC", stations=("MMF1", "MMF9"))
        self.assertFalse(readings.accept("TEST/MMF2/new_sensor", b"100", received_at=900))
        readings.accept("TEST/MMF1/new_sensor", b"-5", received_at=900)
        readings.accept("TEST/MMF9/new_sensor", b"5", received_at=900)
        summary = average_snapshot(readings.snapshot(901))
        self.assertEqual(summary["stationCount"], 2)
        self.assertEqual(summary["parameters"][0]["averageValue"], 0)

    def test_raw_values_unchanged_and_invalid_float_excluded(self):
        readings = Readings("TEST", "SYNTHETIC")
        readings.accept("TEST/MMF1/ghi", b"100", received_at=900)
        snapshot = readings.snapshot(901)
        snapshot["stations"][1]["messages"] = [{"topic":"TEST/MMF2/ghi","value":math.inf,"retained":False,"receivedAt":"1970-01-01T00:15:00Z"}]
        summary = average_snapshot(snapshot)
        self.assertEqual(summary["parameters"][0]["averageValue"], 100)
        self.assertEqual(snapshot["stations"][0]["messages"][0]["rawPayload"], "100")

    @patch.dict(os.environ, {"SUPABASE_URL":"https://synthetic.supabase.co", "SUPABASE_SECRET_KEY":"sb_secret_synthetic_test"})
    def test_rpc_contract_no_raw_values_and_no_redirect(self):
        session = Mock()
        session.post.return_value.status_code = 200
        session.post.return_value.json.return_value = {"parameterCount": 1}
        readings = Readings("TEST", "SYNTHETIC")
        readings.accept("TEST/MMF1/ghi", b"100", received_at=900)
        summary = average_snapshot(readings.snapshot(901))
        write_averages(summary, session)
        args, kwargs = session.post.call_args
        self.assertTrue(args[0].endswith("/rest/v1/rpc/store_mmf_capture"))
        self.assertFalse(kwargs["allow_redirects"])
        self.assertNotIn("Authorization", kwargs["headers"])
        self.assertNotIn("rawPayload", str(kwargs["json"]))
        self.assertEqual(kwargs["json"]["p_captured_at"], "1970-01-01T00:15:01Z")
        session.post.return_value.status_code = 401
        with self.assertRaisesRegex(RuntimeError, "Supabase write failed"):
            write_averages(summary, session)
        with patch.dict(os.environ, {"SUPABASE_SECRET_KEY":"sb_publishable_synthetic"}):
            with self.assertRaises(ValueError):
                write_averages(summary, session)

    def test_supabase_failure_does_not_report_success(self):
        readings = Readings("TEST", "SYNTHETIC")
        readings.set_connected(True)
        readings.accept("TEST/MMF", b'{"MMF1":{"mw":1},"MMF2":{"mw":2},"MMF3":{"mw":3},"MMF4":{"mw":4}}')
        stop = Mock()
        stop.wait.return_value = False
        with patch("collector.deliver_averages", return_value=False), patch("collector.deliver") as file_upload:
            self.assertEqual(collect_once({"supabaseEnabled":True}, readings, stop, 180), 4)
            file_upload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
