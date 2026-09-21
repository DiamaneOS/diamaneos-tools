"""Real child-process lifecycle and bounded streaming regressions."""
import os
import signal
import subprocess
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

    def test_cli_sigterm_unwinds_owned_worker_group(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ready, escaped = root / 'ready', root / 'escaped'
            child = "import pathlib,time; pathlib.Path(" + repr(str(ready)) + ").touch(); time.sleep(1); pathlib.Path(" + repr(str(escaped)) + ").touch()"
            script = ("from diamaneos_tools import process; import sys\n"
                      "with process.interrupt_on_termination():\n"
                      " r=process.run([sys.executable,'-c'," + repr(child) + "],10)\n"
                      " print(r['transport'],flush=True)\n")
            env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / 'src'))
            parent = subprocess.Popen([sys.executable,'-c',script],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 5
                while not ready.exists() and time.monotonic() < deadline: time.sleep(.01)
                self.assertTrue(ready.exists())
                parent.send_signal(signal.SIGTERM)
                stdout, stderr = parent.communicate(timeout=5)
                self.assertEqual(b'interrupted',stdout.strip(),stderr)
                time.sleep(1.05)
                self.assertFalse(escaped.exists())
            finally:
                if parent.poll() is None: parent.kill(); parent.wait()

    def test_large_log_is_streamed_with_a_small_tail_and_hard_total_cap(self):
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "run.log"
            result = process.run([sys.executable, "-c", "print('x'*8192)"],
                                 5, 4096, log_path=log, capture_bytes=128)
            self.assertEqual("overflow", result["transport"])
            self.assertEqual(128, len(result["stdout"]))
            self.assertEqual(4096, log.stat().st_size)
            self.assertFalse(log.stat().st_mode & 0o027)
