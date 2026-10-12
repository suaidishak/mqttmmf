import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from collector import Readings, collect_once, deliver, environment_config, load_config, next_boundary, upload_snapshot


class CollectorTests(unittest.TestCase):
    def test_station_filter_and_raw_values(self):
        readings = Readings("BDNS", "LSS-BIDOR")
        self.assertFalse(readings.accept("BDNS/PPC/power", b"1"))
        self.assertFalse(readings.accept("OTHER/MMF1/ghi", b"1"))
        self.assertFalse(readings.accept("BDNS/MMF5/ghi", b"1"))
        raw = b'{"value": -2.50, "timestamp": "source-time"}'
        self.assertTrue(readings.accept("BDNS/MMF1/ghi", raw, received_at=900))
        result = readings.snapshot(901)
        message = result["stations"][0]["messages"][0]
        self.assertEqual(message["rawPayload"], raw.decode())
        self.assertEqual(message["value"]["value"], -2.5)
        self.assertEqual(message["receivedAt"], "1970-01-01T00:15:00Z")

    def test_all_four_aggregate_and_subtopics(self):
        readings = Readings("BDNS", "LSS-BIDOR")
        readings.accept("BDNS/MMF", b'{"MMF1":{"ghi":1},"MMF2":{"ghi":2},"MMF3":{"ghi":3},"MMF4":{"ghi":4}}', received_at=900)
        readings.accept("BDNS/MMF4/ws", b"6.5", received_at=901)
        readings.set_connected(True)
        snapshot = readings.snapshot(902)
        self.assertEqual([s["status"] for s in snapshot["stations"]], ["fresh"] * 4)
        self.assertEqual(len(snapshot["stations"][3]["messages"]), 2)

    def test_empty_stale_retained_and_disconnect(self):
        readings = Readings("BDNS", "LSS-BIDOR")
        self.assertTrue(all(s["status"] == "missing" for s in readings.snapshot(900)["stations"]))
        readings.accept("BDNS/MMF1", b"1", retained=True, received_at=900)
        readings.accept("BDNS/MMF2", b"2", received_at=900)
        readings.set_connected(True)
        self.assertEqual(readings.snapshot(901)["stations"][0]["status"], "staleOrUnverified")
        self.assertEqual(readings.snapshot(1801)["stations"][1]["status"], "staleOrUnverified")
        readings.set_connected(False)
        self.assertEqual(readings.snapshot(901)["stations"][1]["status"], "staleOrUnverified")

    def test_invalid_and_bounded_messages(self):
        readings = Readings("BDNS", "LSS-BIDOR")
        for payload in (b"NaN", b"not json", b"\xff", b"1" * 65537):
            self.assertFalse(readings.accept("BDNS/MMF1", payload))
        self.assertFalse(readings.accept("BDNS/MMF", b'{"PPC":1}'))
        for number in range(256):
            self.assertTrue(readings.accept(f"BDNS/MMF1/{number}", b"1"))
        self.assertFalse(readings.accept("BDNS/MMF1/overflow", b"1"))
        self.assertTrue(readings.accept("BDNS/MMF1/0", b"2"))

    def test_utc_quarter_schedule(self):
        self.assertEqual(next_boundary(0), 900)
        self.assertEqual(next_boundary(899.99), 900)
        self.assertEqual(next_boundary(900), 1800)

    @patch.dict(os.environ, {"MQTT_USERNAME": "synthetic-user", "MQTT_PASSWORD": "synthetic-password", "PYTHONANYWHERE_API_TOKEN": "synthetic-token"})
    def test_config_no_public_sample_directory(self):
        config = {"host": "example.invalid", "port": 1883, "tls": False, "siteCode": "LSS-BIDOR",
                  "topicRoot": "BDNS", "pythonAnywhereUsername": "suaidishak",
                  "remoteFile": "/home/suaidishak/mysite/mqttmmf/mmf_latest.json"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8-sig")
            self.assertEqual(load_config(path)["topicRoot"], "BDNS")
            for remote in ("/home/suaidishak/sample_files/mmf.json", "/home/other/mmf.json", "/home/suaidishak/../mmf.json"):
                config["remoteFile"] = remote
                path.write_text(json.dumps(config))
                with self.assertRaises(ValueError):
                    load_config(path)
            with patch.dict(os.environ, {"MQTT_PASSWORD": ""}):
                with self.assertRaises(ValueError):
                    load_config(path)

    @patch.dict(os.environ, {"PYTHONANYWHERE_API_TOKEN": "synthetic-token"})
    def test_upload_contract_and_errors(self):
        from unittest.mock import Mock
        session = Mock()
        session.post.return_value.status_code = 201
        config = {"pythonAnywhereUsername": "suaidishak", "remoteFile": "/home/suaidishak/mysite/mqttmmf/mmf_latest.json"}
        readings = Readings("BDNS", "LSS-BIDOR")
        readings.accept("BDNS/MMF1/ghi", b"123", received_at=900)
        upload_snapshot(config, readings.snapshot(901), session)
        args, kwargs = session.post.call_args
        self.assertEqual(args[0], "https://www.pythonanywhere.com/api/v0/user/suaidishak/files/path/home/suaidishak/mysite/mqttmmf/mmf_latest.json")
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(json.loads(kwargs["files"]["content"][1])["stations"][0]["messages"][0]["value"], 123)
        for code in (401, 403, 302, 500):
            session.post.return_value.status_code = code
            with self.assertRaisesRegex(RuntimeError, "Upload failed"):
                upload_snapshot(config, readings.snapshot(901), session)

    def test_bounded_retries_and_shutdown(self):
        from unittest.mock import Mock
        stop = Mock()
        stop.wait.return_value = False
        with patch("collector.upload_snapshot", side_effect=RuntimeError("synthetic failure")) as upload:
            self.assertFalse(deliver({}, {}, stop))
            self.assertEqual(upload.call_count, 3)
        stop = threading.Event()
        stop.set()
        with patch("collector.upload_snapshot") as upload:
            self.assertFalse(deliver({}, {}, stop))
            upload.assert_not_called()

    def test_once_missing_station_preserves_remote_file(self):
        from unittest.mock import Mock
        readings = Readings("BDNS", "LSS-BIDOR")
        readings.set_connected(True)
        readings.accept("BDNS/MMF1", b"1")
        stop = Mock()
        stop.wait.return_value = False
        with patch("collector.deliver") as upload:
            self.assertEqual(collect_once({}, readings, stop, 180), 2)
            upload.assert_not_called()
        stop.wait.assert_called_once_with(180)

    def test_once_complete_retained_upload_and_probe(self):
        from unittest.mock import Mock
        readings = Readings("BDNS", "LSS-BIDOR")
        readings.set_connected(True)
        readings.accept("BDNS/MMF", b'{"MMF1":1,"MMF2":2,"MMF3":3,"MMF4":4}', retained=True)
        stop = Mock()
        stop.wait.return_value = False
        with patch("collector.deliver", return_value=True) as upload:
            self.assertEqual(collect_once({}, readings, stop, 180, no_upload=True), 0)
            upload.assert_not_called()
            self.assertEqual(collect_once({}, readings, stop, 180), 0)
            snapshot = upload.call_args.args[1]
            self.assertTrue(all(s["status"] == "staleOrUnverified" for s in snapshot["stations"]))
            upload.return_value = False
            self.assertEqual(collect_once({}, readings, stop, 180), 3)

    def test_once_disconnected_and_cancelled(self):
        from unittest.mock import Mock
        readings = Readings("BDNS", "LSS-BIDOR")
        readings.accept("BDNS/MMF", b'{"MMF1":1,"MMF2":2,"MMF3":3,"MMF4":4}')
        stop = Mock()
        stop.wait.return_value = False
        with patch("collector.deliver") as upload:
            self.assertEqual(collect_once({}, readings, stop, 180), 2)
            stop.wait.return_value = True
            self.assertEqual(collect_once({}, readings, stop, 180), 130)
            upload.assert_not_called()

    @patch.dict(os.environ, {"MQTT_HOST": "example.invalid", "MQTT_USERNAME": "synthetic-user", "MQTT_PASSWORD": "synthetic-password",
                            "PYTHONANYWHERE_API_TOKEN": "synthetic-token", "PYTHONANYWHERE_USERNAME": "testuser",
                            "PYTHONANYWHERE_REMOTE_FILE": "/home/testuser/mysite/mqttmmf/mmf_github_candidate.json",
                            "MQTT_TLS": "true", "MQTT_PORT": "8883"})
    def test_actions_environment_configuration(self):
        self.assertTrue(environment_config()["tls"])
        self.assertEqual(environment_config()["port"], 8883)
        with patch.dict(os.environ, {"MQTT_TLS": "invalid"}):
            with self.assertRaises(ValueError):
                environment_config()
        with patch.dict(os.environ, {"PYTHONANYWHERE_API_TOKEN": ""}):
            with self.assertRaises(ValueError):
                environment_config()


if __name__ == "__main__":
    unittest.main()
