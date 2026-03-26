from __future__ import annotations

import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from polymarket_lower_win.log_paths import local_run_stamp, normalize_logs_root
from polymarket_lower_win.paper import PaperConfig, PaperSimulator


class LogPathsTests(unittest.TestCase):
    def test_normalize_logs_root_to_lowercase_logs(self) -> None:
        self.assertEqual(
            normalize_logs_root("Logs/paper_low_win", default_subdir="paper_low_win"),
            Path("logs/paper_low_win"),
        )
        self.assertEqual(
            normalize_logs_root("logs/Logs/paper_low_win", default_subdir="paper_low_win"),
            Path("logs/paper_low_win"),
        )

    def test_local_run_stamp_is_second_precision(self) -> None:
        stamp = local_run_stamp(1773482705)
        self.assertEqual(len(stamp), 14)
        self.assertTrue(stamp.isdigit())

    def test_paper_simulator_rotates_to_midnight_directory(self) -> None:
        now_local = datetime.fromtimestamp(time.time()).astimezone()
        tomorrow_midnight = (now_local + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)

        with tempfile.TemporaryDirectory() as tmp:
            cfg = PaperConfig(
                run_id="20260326093015",
                logs_root=str(Path(tmp) / "logs" / "paper_low_win"),
            )
            runner = PaperSimulator(cfg)
            runner._rotate_logs_if_needed(int(tomorrow_midnight.timestamp()))
            self.assertEqual(runner.run_dir.name, tomorrow_midnight.strftime("%Y%m%d000000"))
            self.assertTrue(runner.snapshots_path.exists())
            self.assertTrue(runner.state_path.exists())


if __name__ == "__main__":
    unittest.main()
