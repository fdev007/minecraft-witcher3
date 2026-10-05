"""supervise.py: когда перезапускать упавшие сервер и клиент MC. Скрипты не запускаются — подменный runner."""
import time
import unittest

from supervise import Restarter


class FakeRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, script, args, timeout):
        self.calls.append(list(args))
        return 0, "ok"


def wait_idle(r, timeout=2.0):
    end = time.time() + timeout
    while r.busy and time.time() < end:
        time.sleep(0.01)
    return not r.busy


class RestarterTest(unittest.TestCase):
    def setUp(self):
        self.run = FakeRunner()
        self.r = Restarter("клиент", "x.ps1", down_after_s=15.0, min_interval_s=90.0, timeout_s=10.0, runner=self.run)

    def test_waits_then_stop_and_start(self):
        r = self.r
        self.assertFalse(r.step(0.0, ok=False))
        self.assertFalse(r.step(14.0, ok=False))                  # ещё рано
        self.assertTrue(r.step(15.5, ok=False))
        self.assertTrue(wait_idle(r))
        self.assertEqual(self.run.calls, [["-Stop"], []])         # сначала погасить, потом запустить
        self.assertEqual(r.restarts, 1)

    def test_recovery_resets_and_interval_limits(self):
        r = self.r
        r.step(0.0, ok=False)
        r.step(10.0, ok=True)                                     # связь вернулась — отсчёт заново
        self.assertFalse(r.step(16.0, ok=False))
        self.assertFalse(r.step(30.0, ok=False))
        self.assertTrue(r.step(32.0, ok=False))
        wait_idle(r)
        r.step(40.0, ok=False)
        self.assertFalse(r.step(100.0, ok=False))                 # не чаще min_interval_s
        self.assertTrue(r.step(123.0, ok=False))
        wait_idle(r)
        self.assertEqual(r.restarts, 2)

    def test_not_allowed(self):
        r = self.r
        r.step(0.0, ok=False, allowed=False)
        self.assertFalse(r.step(60.0, ok=False, allowed=False))   # клиенту без сервера рестарт не поможет
        self.assertTrue(r.step(61.0, ok=False, allowed=True))
        wait_idle(r)


if __name__ == "__main__":
    unittest.main()
