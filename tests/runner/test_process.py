"""Real child-process lifecycle and bounded streaming regressions."""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from diamaneos_tools import process


class ProcessTest(unittest.TestCase):
    def test_exited_leader_does_not_leave_same_group_child_running(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / "escaped"
            child = "import time,pathlib; time.sleep(.8); pathlib.Path(" + repr(str(marker)) + ").touch()"
            parent = "import subprocess,sys; subprocess.Popen([sys.executable,'-c'," + repr(child) + "],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)"
            result = process.run([sys.executable, "-c", parent], 2)
            self.assertEqual("ok", result["transport"])
            time.sleep(.85)
            self.assertFalse(marker.exists())

    def test_term_resistant_descendant_is_killed_after_grace(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / "escaped"
            script = ("import os,signal,time,pathlib; p=os.fork(); "
                      "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                      "print('ready',flush=True); time.sleep(.9); pathlib.Path(" + repr(str(marker)) + ").touch()")
            result = process.run([sys.executable, "-c", script], .15)
            self.assertEqual("timeout", result["transport"])
            time.sleep(.95)
            self.assertFalse(marker.exists())

    def test_large_log_is_streamed_with_a_small_tail_and_hard_total_cap(self):
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "run.log"
            result = process.run([sys.executable, "-c", "print('x'*8192)"],
                                 5, 4096, log_path=log, capture_bytes=128)
            self.assertEqual("overflow", result["transport"])
            self.assertEqual(128, len(result["stdout"]))
            self.assertEqual(4096, log.stat().st_size)
            self.assertFalse(log.stat().st_mode & 0o027)
