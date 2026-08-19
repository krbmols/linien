"""Tests for the autolock's `watch_lock_delay`.

Plain script, like the other tests added for this lab: run it with

    PYTHONPATH=. python tests/test_watch_lock_delay.py

It prints `ok` per case and exits non-zero on the first failure.

The point of the delay is that something else in the experiment (RF
evaporation) kicks the control signal for a bounded time and it comes back on
its own. The watcher callback fires several times a second, so a fake clock is
used here rather than real time: the excursion has to be judged in *seconds*,
not in frames, and a fixture that advanced the clock once per frame would hide
exactly that bug.
"""
import sys
import numpy as np

import linien.server.autolock as autolock_module
from linien.server.autolock import Autolock
from linien.server.parameters import Parameters


FRAMES_PER_SECOND = 8


class FakeClock:
    """A clock the test moves by hand."""

    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeControl:
    def __init__(self, parameters):
        self.parameters = parameters

    def pause_acquisition(self):
        pass

    def continue_acquisition(self):
        pass

    def exposed_write_data(self):
        pass

    def exposed_start_lock(self):
        pass

    def exposed_start_ramp(self):
        pass


class Watcher:
    """An `Autolock` reduced to the lock-watching it does after locking."""

    def __init__(self, clock, threshold=0.01, delay=0, start_value=0.0):
        self.parameters = Parameters()
        self.parameters.watch_lock_threshold.value = threshold
        self.parameters.watch_lock_delay.value = delay

        self.autolock = Autolock(FakeControl(self.parameters), self.parameters)
        self.autolock.watcher_last_value = start_value
        self.relocks = 0

        # `relock()` restarts the whole approach, which is not what is under
        # test here. Count the calls and leave the watcher in the state
        # `after_lock()` would put it in once the relock finished.
        def fake_relock():
            self.relocks += 1
            self.autolock.watcher_last_value = self.last_mean
            self.autolock._reset_watcher_delay()

        self.autolock.relock = fake_relock
        self.clock = clock
        self.last_mean = start_value

    def feed(self, value, seconds=None):
        """Feed one frame whose control signal sits at `value`.

        With `seconds` given, feed that many seconds' worth of frames instead,
        advancing the fake clock as the acquisition would.
        """
        if seconds is None:
            self.last_mean = value
            self.autolock.watch_lock(np.zeros(100), np.full(100, value * 8192))
            return

        for _ in range(int(round(seconds * FRAMES_PER_SECOND))):
            self.clock.advance(1.0 / FRAMES_PER_SECOND)
            self.feed(value)


failures = []


def check(name, condition):
    if condition:
        print('ok   - ' + name)
    else:
        print('FAIL - ' + name)
        failures.append(name)


def main():
    clock = FakeClock()
    autolock_module.time = clock

    # --- delay = 0 keeps the historic behaviour -------------------------
    w = Watcher(clock, threshold=0.01, delay=0)
    w.feed(0.0, seconds=1)
    check('delay=0: a steady control signal does not relock', w.relocks == 0)

    w = Watcher(clock, threshold=0.01, delay=0)
    w.feed(0.05)
    check('delay=0: a jump past the threshold relocks at once', w.relocks == 1)

    w = Watcher(clock, threshold=0.01, delay=0)
    w.feed(0.005)
    check('delay=0: a jump below the threshold does not relock', w.relocks == 0)

    # --- a transient that recovers inside the delay is ridden out -------
    w = Watcher(clock, threshold=0.01, delay=5)
    w.feed(0.0, seconds=1)
    w.feed(0.05, seconds=3)          # RF evaporation kicks the control signal
    check('delay=5: no relock while the excursion is younger than the delay',
          w.relocks == 0)
    w.feed(0.0, seconds=3)           # and it comes back by itself
    check('delay=5: an excursion that recovers costs no relock', w.relocks == 0)

    # the watcher has to be usable afterwards
    w.feed(0.0, seconds=1)
    check('delay=5: watching continues after a recovered excursion',
          w.relocks == 0)
    w.feed(0.05, seconds=6)
    check('delay=5: a later genuine loss still relocks', w.relocks == 1)

    # --- an excursion that stays put does relock ------------------------
    # Note the shape of this case: after the first frame of the step the
    # frame-to-frame difference is zero again. Only a comparison against the
    # value the signal jumped *away* from can still see the excursion.
    w = Watcher(clock, threshold=0.01, delay=5)
    w.feed(0.0, seconds=1)
    w.feed(0.05, seconds=4)
    check('delay=5: a persistent step has not relocked before the delay is up',
          w.relocks == 0)
    w.feed(0.05, seconds=2)
    check('delay=5: a persistent step relocks once the delay is up',
          w.relocks == 1)

    # --- the delay is in seconds, not in frames -------------------------
    # Many frames arrive per second; counting frames rather than seconds is
    # the mistake this guards against.
    w = Watcher(clock, threshold=0.01, delay=30)
    w.feed(0.0, seconds=1)
    w.feed(0.05, seconds=10)
    check('a 30 s delay is not used up by 10 s worth of frames', w.relocks == 0)

    # --- railing never waits --------------------------------------------
    w = Watcher(clock, threshold=0.01, delay=60)
    w.feed(0.0, seconds=1)
    w.feed(0.99)
    check('railing the output relocks immediately despite the delay',
          w.relocks == 1)

    # railing while already waiting out an excursion is still immediate
    w = Watcher(clock, threshold=0.01, delay=60)
    w.feed(0.0, seconds=1)
    w.feed(0.05, seconds=2)
    w.feed(0.99)
    check('railing during the delay relocks immediately', w.relocks == 1)

    # --- a slow walk still slips through, as it always did ---------------
    # `watch_lock` is a rate detector; the delay does not change that. Stated
    # here so the behaviour is documented rather than assumed.
    w = Watcher(clock, threshold=0.01, delay=5)
    for i in range(50):
        w.feed(0.005 * i)
    check('a drift slower than the threshold per frame still does not relock',
          w.relocks == 0)

    if failures:
        print('\n%d check(s) failed' % len(failures))
        return 1

    print('\nall checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
