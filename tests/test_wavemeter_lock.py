"""Tests for the wavemeter lock, run without a RedPitaya or a wavemeter.

    python tests/test_wavemeter_lock.py

A simulated laser stands in for both: its frequency follows the ramp centre
through a tuning coefficient the lock is not told about, and a stub wavemeter
reports that frequency the way the real /api/latest route would.
"""

import pickle
import sys
from os import path
from time import time

import numpy as np

sys.path.insert(0, path.join(path.dirname(path.abspath(__file__)), '..'))

from linien.common import get_lock_point
from linien.server.parameters import Parameters
from linien.server.wavemeter import build_url, read_once, WavemeterError, \
    WavemeterMonitor
from linien.server.wavemeter_lock import WavemeterLock

Y_SHIFT = 4000
SETPOINT = 309602.628

failures = []


def check(name, condition, detail=''):
    if condition:
        print('  ok   %s' % name)
    else:
        print('  FAIL %s %s' % (name, detail))
        failures.append(name)


# --------------------------------------------------------------- fixtures

def peak(x):
    return np.exp(-np.abs(x)) * np.sin(x)


def spectrum_for_testing(x):
    central_peak = peak(x) * 2048
    smaller_peaks = (peak(x - 10) * 1024) - (peak(x + 10) * 1024)
    return central_peak + smaller_peaks + Y_SHIFT


def get_signal(ramp_amplitude, center, shift=0.0):
    max_val = np.pi * 5 * ramp_amplitude
    new_center = center + shift
    x = np.linspace((-1 + new_center) * max_val, (1 + new_center) * max_val, 16384)
    return spectrum_for_testing(x)


class FakeControl:
    """The bits of the server a lock task touches."""

    def __init__(self, parameters):
        self.parameters = parameters
        self.locked = False

    def pause_acquisition(self):
        pass

    def continue_acquisition(self):
        pass

    def exposed_write_data(self):
        pass

    def exposed_start_ramp(self):
        pass

    def exposed_start_lock(self):
        self.locked = True


class FakeLaser:
    """Frequency follows the ramp centre; the lock has to work the slope out.

    GHz_per_V is deliberately negative for most tests: a sign error in the
    steering would run the laser away from the setpoint instead of towards it.
    """

    def __init__(self, parameters, GHz_per_V=-3.0, offset_GHz=4.0, dark=False,
                 unreachable=False, drift_per_read_GHz=0.0):
        self.parameters = parameters
        self.GHz_per_V = GHz_per_V
        self.dark = dark
        self.unreachable = unreachable
        self.center_at_start = parameters.center.value
        self.offset_GHz = offset_GHz
        # Frequency walks by this much on every reading, so a laser can be
        # caught passing through the handoff window rather than sitting in it.
        self.drift_per_read_GHz = drift_per_read_GHz
        # Wander is different from drift: it moves the reading about without
        # going anywhere, which is what a free-running laser does and what a
        # settle test has to tolerate.  Steering cannot chase it away.
        self.wander_GHz = 0.0
        self._wander_sign = 1
        self.clock = 1_000_000.0

    @property
    def frequency(self):
        moved = self.parameters.center.value - self.center_at_start
        return (SETPOINT + self.offset_GHz + moved * self.GHz_per_V
                + self._wander_sign * self.wander_GHz)

    def read(self, base_url, setpoint, search_range, use_raw=True, timeout=None):
        """Stands in for linien.server.wavemeter.read_once."""
        if self.unreachable:
            raise WavemeterError('cannot reach %s' % base_url)
        if self.dark:
            return None

        detuning_GHz = self.frequency - setpoint
        if abs(detuning_GHz) > search_range:
            return None

        self.offset_GHz += self.drift_per_read_GHz
        self._wander_sign = -self._wander_sign

        self.clock += 1.0
        return {
            'raw_median_GHz': self.frequency,
            'median_GHz': self.frequency,
            'detuning_MHz': detuning_GHz * 1e3,
            'age_s': 0.01,
            'time': self.clock,
            'used': 'raw' if use_raw else 'calibrated',
        }


class StubMonitor:
    """A WavemeterMonitor that polls inline instead of on a thread.

    ``reads_per_poll`` is what makes this honest: the real monitor fetches once
    a second while the lock looks at it on every acquisition frame, so the same
    answer -- and the same arrival time -- comes back several times in a row.
    A stub that invents a fresh answer per look hides every bug that depends on
    telling "no news" from "news".
    """

    def __init__(self, laser, parameters, reads_per_poll=1):
        self.laser = laser
        self.parameters = parameters
        self.reads_per_poll = reads_per_poll
        self.started = False
        self.stopped = False
        self.looks = 0
        # Arrivals are stamped on the same clock the real monitor uses, since
        # the lock compares them against its own settle-time deadlines.
        self.clock = time()
        self.answer = None

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def poll(self):
        # Strictly increasing, so two polls are never mistaken for one.
        self.clock = max(time(), self.clock + 1e-6)
        try:
            reading = self.laser.read(
                self.parameters.wavemeter_url.value,
                self.parameters.wavemeter_setpoint.value,
                self.parameters.wavemeter_search_range.value,
            )
        except WavemeterError as error:
            self.answer = (None, str(error), False, self.clock)
            return
        self.answer = (reading, None, True, self.clock)

    def latest(self):
        if self.looks % self.reads_per_poll == 0:
            self.poll()
        self.looks += 1
        return self.answer


def make_parameters():
    parameters = Parameters()
    parameters.wavemeter_setpoint.value = SETPOINT
    parameters.wavemeter_range.value = 100.0         # MHz
    parameters.wavemeter_search_range.value = 20.0   # GHz
    parameters.wavemeter_handoff_window.value = 200.0  # MHz
    parameters.wavemeter_settled_within.value = 20.0   # MHz
    parameters.wavemeter_settle_time.value = 0.0
    parameters.wavemeter_lock_settle_time.value = 0.0
    parameters.ramp_amplitude.value = 1
    parameters.center.value = 0
    return parameters


def start_lock(parameters, laser, control=None):
    control = control or FakeControl(parameters)
    monitor = StubMonitor(laser, parameters)
    lock = WavemeterLock(control, parameters, monitor=monitor,
                         wait_time_between_current_corrections=0)

    # Recorded with the line in view, as it is when the user drags over it.
    spectrum = get_signal(parameters.ramp_amplitude.value, 0.0)
    # Select the central peak, the same window test_autolock.py drags over.
    middle = len(spectrum) // 2
    margin = int(0.01 * len(spectrum))
    lock.run(middle - margin, middle + margin, spectrum, auto_offset=False)
    return lock, control, monitor


def feed_until(lock, parameters, laser, predicate, limit=200):
    """Feed frames until `predicate` holds, so a test can watch one stage."""
    for _ in range(limit):
        if predicate():
            return True
        feed(lock, parameters, laser, 1)
    return predicate()


def feed(lock, parameters, laser, n=1):
    """Hand the lock n acquisition frames, as the server's to_plot would.

    The spectroscopy line sits at the setpoint frequency, so where it appears
    in the scan follows the laser rather than the ramp centre directly.  That
    ties the two stages to one physical quantity: steering the laser onto the
    setpoint also walks the line into the middle of the scan, which is what
    the correlation approacher then refines.
    """
    for _ in range(n):
        if laser.GHz_per_V:
            # Distance from the line, expressed in the volts of ramp centre it
            # would take to close it.
            offset = (laser.frequency - SETPOINT) / laser.GHz_per_V
        else:
            offset = parameters.center.value
        signal = get_signal(parameters.ramp_amplitude.value, offset)
        lock.react_to_new_spectrum(pickle.dumps({
            'error_signal_1': signal,
            'error_signal_2': signal * 0,
        }))


# ------------------------------------------------------------------ tests

print('url building')
check('endpoint appended',
      build_url('http://host:8050', 500.0, 1.0)
      == 'http://host:8050/api/latest?freq=500.000000&tol=1.000000'
         '&min_amp=0.0500&raw=1',
      '(got %s)' % build_url('http://host:8050', 500.0, 1.0))
check('amplitude floor passed through',
      '&min_amp=0.0050' in build_url('http://host:8050', 500.0, 1.0, min_amp=0.005))
check('trailing slash tolerated',
      build_url('http://host:8050/', 500.0, 1.0).startswith(
          'http://host:8050/api/latest'))
check('full endpoint not doubled',
      build_url('http://host:8050/api/latest', 500.0, 1.0).count('/api/latest') == 1)
check('calibrated when asked',
      '&raw=1' not in build_url('http://host:8050', 500.0, 1.0, use_raw=False))
try:
    build_url('   ', 500.0, 1.0)
    check('empty address rejected', False)
except WavemeterError:
    check('empty address rejected', True)


print('response handling')


class FakeResponse:
    def __init__(self, body):
        self.body = body.encode('utf-8')

    def read(self):
        return self.body

    def close(self):
        pass


def opener_for(body):
    return lambda url, timeout=None: FakeResponse(body)


reading = read_once('http://h', 1.0, 1.0,
                    opener=opener_for('{"found": true, "laser": '
                                      '{"detuning_MHz": 12.5, "used": "raw"}}'))
check('reading parsed', reading['detuning_MHz'] == 12.5)
check('not found is None',
      read_once('http://h', 1.0, 1.0,
                opener=opener_for('{"found": false, "laser": null}')) is None)
for body, why in [('not json at all', 'malformed json'),
                  ('{"found": true, "laser": {"used": "raw"}}',
                   'reading without a detuning'),
                  ('[1, 2, 3]', 'unexpected shape')]:
    try:
        read_once('http://h', 1.0, 1.0, opener=opener_for(body))
        check(why + ' rejected', False)
    except WavemeterError:
        check(why + ' rejected', True)


print('a server that ignores raw=1 is refused')
for body, why in [
    ('{"found": true, "laser": {"detuning_MHz": 1.0, "used": "calibrated"}}',
     'calibrated answer to a raw request'),
    ('{"found": true, "laser": {"detuning_MHz": 1.0}}',
     'unlabelled answer'),
]:
    try:
        read_once('http://h', 1.0, 1.0, opener=opener_for(body))
        check(why + ' rejected', False)
    except WavemeterError as error:
        check(why + ' rejected', 'up to date' in str(error))
check('calibrated request accepts a calibrated answer',
      read_once('http://h', 1.0, 1.0, use_raw=False,
                opener=opener_for('{"found": true, "laser": '
                                  '{"detuning_MHz": 1.0, "used": "calibrated"}}')
                )['detuning_MHz'] == 1.0)


def broken_opener(url, timeout=None):
    raise IOError('connection refused')


try:
    read_once('http://h', 1.0, 1.0, opener=broken_opener)
    check('unreachable raises', False)
except WavemeterError:
    check('unreachable raises', True)
except IOError:
    check('unreachable raises', False, '(leaked IOError)')


print('steering onto the setpoint')
parameters = make_parameters()
laser = FakeLaser(parameters, GHz_per_V=-3.0, offset_GHz=2.0)
lock, control, monitor = start_lock(parameters, laser)

check('monitor started', monitor.started)
check('TTL high while unlocked',
      parameters.gpio_p_out.value == 0b11111111
      and parameters.gpio_n_out.value == 0b11111111)
check('ramp narrowed for steering',
      parameters.ramp_amplitude.value
      == parameters.wavemeter_steering_ramp_amplitude.value,
      '(got %s)' % parameters.ramp_amplitude.value)
check('steering stage active', parameters.wavemeter_lock_steering.value)

reached_handoff = feed_until(
    lock, parameters, laser,
    lambda: parameters.wavemeter_lock_approaching.value
)

check('slope measured', lock.slope_GHz_per_V is not None)
check('slope sign correct',
      lock.slope_GHz_per_V is not None and lock.slope_GHz_per_V < 0,
      '(got %s)' % lock.slope_GHz_per_V)
check('slope roughly right',
      lock.slope_GHz_per_V is not None
      and abs(lock.slope_GHz_per_V - (-3.0)) < 0.5,
      '(got %s)' % lock.slope_GHz_per_V)
check('reached the handoff window',
      abs(laser.frequency - SETPOINT) <= 1.0,
      '(off by %.3f GHz)' % (laser.frequency - SETPOINT))
check('handed over to the approacher',
      reached_handoff and not parameters.wavemeter_lock_steering.value)
check('ramp restored for approaching',
      parameters.ramp_amplitude.value
      == parameters.wavemeter_lock_initial_ramp_amplitude.value,
      '(got %s)' % parameters.ramp_amplitude.value)
check('goes on to lock',
      feed_until(lock, parameters, laser,
                 lambda: parameters.wavemeter_lock_locked.value))

print('steering with the opposite tuning sign')
parameters = make_parameters()
laser = FakeLaser(parameters, GHz_per_V=+2.5, offset_GHz=-2.0)
lock, control, monitor = start_lock(parameters, laser)
feed_until(lock, parameters, laser,
           lambda: parameters.wavemeter_lock_approaching.value)
check('slope sign follows the laser',
      lock.slope_GHz_per_V is not None and lock.slope_GHz_per_V > 0,
      '(got %s)' % lock.slope_GHz_per_V)
check('reached the handoff window',
      abs(laser.frequency - SETPOINT) <= 1.0,
      '(off by %.3f GHz)' % (laser.frequency - SETPOINT))

print('a laser that ignores the ramp centre')
parameters = make_parameters()
laser = FakeLaser(parameters, GHz_per_V=0.0, offset_GHz=4.0)
lock, control, monitor = start_lock(parameters, laser)
feed(lock, parameters, laser, 40)
check('gives up instead of steering blind',
      parameters.wavemeter_lock_failed.value)
check('says why', 'does not follow' in parameters.wavemeter_status.value,
      '(%s)' % parameters.wavemeter_status.value)
check('TTL left high on failure', parameters.gpio_p_out.value == 0b11111111)
check('monitor stopped', monitor.stopped)

print('locking, then watching')
parameters = make_parameters()
laser = FakeLaser(parameters, GHz_per_V=-3.0, offset_GHz=0.0)
lock, control, monitor = start_lock(parameters, laser)
feed(lock, parameters, laser, 60)
check('locked', parameters.wavemeter_lock_locked.value,
      '(status: %s)' % parameters.wavemeter_status.value)
check('TTL low once locked',
      parameters.gpio_p_out.value == 0b00000000
      and parameters.gpio_n_out.value == 0b00000000)
check('watching', parameters.wavemeter_lock_watching.value)

feed(lock, parameters, laser, 10)
check('stays locked while on setpoint',
      parameters.gpio_p_out.value == 0b00000000
      and lock.out_of_range_count == 0)

print('a drift out of range triggers a relock')
laser.offset_GHz = 0.5  # 500 MHz, well outside the 100 MHz window
feed(lock, parameters, laser, parameters.wavemeter_max_out_of_range.value - 1)
check('one reading short of the limit does not relock',
      parameters.gpio_p_out.value == 0b00000000,
      '(relocked after %d)' % lock.out_of_range_count)
feed(lock, parameters, laser, 1)
check('relocked at the limit',
      parameters.gpio_p_out.value == 0b11111111)
check('back to steering', parameters.wavemeter_lock_steering.value)
check('marked as retrying', parameters.wavemeter_lock_retrying.value)

print('a single excursion does not relock')
parameters = make_parameters()
laser = FakeLaser(parameters, GHz_per_V=-3.0, offset_GHz=0.0)
lock, control, monitor = start_lock(parameters, laser)
feed(lock, parameters, laser, 60)
laser.offset_GHz = 0.5
feed(lock, parameters, laser, 2)
laser.offset_GHz = 0.0
feed(lock, parameters, laser, 2)
check('counter resets on a good reading', lock.out_of_range_count == 0)
check('still locked', parameters.gpio_p_out.value == 0b00000000)

print('an unreachable wavemeter holds the lock')
parameters = make_parameters()
laser = FakeLaser(parameters, GHz_per_V=-3.0, offset_GHz=0.0)
lock, control, monitor = start_lock(parameters, laser)
feed(lock, parameters, laser, 60)
check('locked first', parameters.wavemeter_lock_locked.value)

laser.unreachable = True
feed(lock, parameters, laser, 20)
check('lock held, TTL still low',
      parameters.gpio_p_out.value == 0b00000000)
check('flagged as stale', parameters.wavemeter_stale.value)
check('status explains the hold',
      parameters.wavemeter_status.value.startswith('holding:'),
      '(%s)' % parameters.wavemeter_status.value)
check('no relock counted', lock.out_of_range_count == 0)

laser.unreachable = False
feed(lock, parameters, laser, 2)
check('recovers when the wavemeter comes back',
      not parameters.wavemeter_stale.value
      and parameters.gpio_p_out.value == 0b00000000)

print('a laser the wavemeter cannot see is treated as gone')
parameters = make_parameters()
laser = FakeLaser(parameters, GHz_per_V=-3.0, offset_GHz=0.0)
lock, control, monitor = start_lock(parameters, laser)
feed(lock, parameters, laser, 60)
laser.dark = True
feed(lock, parameters, laser, parameters.wavemeter_max_out_of_range.value)
check('relocks rather than holding',
      parameters.gpio_p_out.value == 0b11111111)
check('not flagged stale (the wavemeter answered)',
      not parameters.wavemeter_stale.value)

print('watch disabled stops instead of relocking')
parameters = make_parameters()
parameters.watch_wavemeter_lock.value = False
laser = FakeLaser(parameters, GHz_per_V=-3.0, offset_GHz=0.0)
lock, control, monitor = start_lock(parameters, laser)
feed(lock, parameters, laser, 60)
laser.offset_GHz = 0.5
feed(lock, parameters, laser, parameters.wavemeter_max_out_of_range.value)
check('failed', parameters.wavemeter_lock_failed.value)
check('not running any more', not parameters.wavemeter_lock_running.value)
check('TTL high', parameters.gpio_p_out.value == 0b11111111)

print('stopping cleans up')
parameters = make_parameters()
laser = FakeLaser(parameters, GHz_per_V=-3.0, offset_GHz=0.0)
lock, control, monitor = start_lock(parameters, laser)
lock.stop()
check('monitor stopped', monitor.stopped)
check('task cleared', parameters.task.value is None)
check('TTL high', parameters.gpio_p_out.value == 0b11111111)
check('ramp restored',
      parameters.ramp_amplitude.value
      == parameters.wavemeter_lock_initial_ramp_amplitude.value)

print('the TTL drops as soon as the line is centred')
# The wavemeter cannot judge a scanning laser -- it measures the sweep, not the
# line -- so the lockbox is handed the laser first and asked about afterwards.
parameters = make_parameters()
laser = FakeLaser(parameters, GHz_per_V=-3.0, offset_GHz=0.0)
lock, control, monitor = start_lock(parameters, laser)
feed_until(lock, parameters, laser,
           lambda: parameters.wavemeter_lock_confirming.value)
check('engaged, pending verification',
      parameters.wavemeter_lock_confirming.value)
check('TTL already low', parameters.gpio_p_out.value == 0b00000000)
check('not yet counted as locked',
      not parameters.wavemeter_lock_watching.value)

feed(lock, parameters, laser, 5)
check('verified and locked', parameters.wavemeter_lock_locked.value)
check('TTL stays low', parameters.gpio_p_out.value == 0b00000000)
check('watching', parameters.wavemeter_lock_watching.value)
check('verification cleared', not parameters.wavemeter_lock_confirming.value)

print('a laser still moving does not trigger the handoff')
# Drifting steadily through the window: it is inside on some readings, but it
# is not settled, and the line search must not be handed a moving target.
parameters = make_parameters()
laser = FakeLaser(parameters, GHz_per_V=-3.0, offset_GHz=-0.6,
                  drift_per_read_GHz=0.08)
lock, control, monitor = start_lock(parameters, laser)
inside_seen = 0
for _ in range(40):
    feed(lock, parameters, laser, 1)
    if abs(laser.frequency - SETPOINT) * 1e3 <= \
            parameters.wavemeter_handoff_window.value:
        inside_seen += 1
    if parameters.wavemeter_lock_approaching.value:
        break
check('the laser did pass through the window', inside_seen > 0,
      '(never entered it, test says nothing)')
check('did not hand over while moving',
      not parameters.wavemeter_lock_approaching.value,
      '(handed over after %d readings inside)' % inside_seen)

print('a laser that never sits perfectly still still gets locked')
# The real complaint: a free-running laser wanders, and a settle threshold
# tighter than that wander means it is never handed over at all.
parameters = make_parameters()
parameters.wavemeter_skip_line_search.value = True
parameters.wavemeter_settled_within.value = 5.0     # tighter than the wander
laser = FakeLaser(parameters, GHz_per_V=-3.0, offset_GHz=0.04)
laser.wander_GHz = 0.01                             # +-10 MHz, so 20 MHz apart
lock, control, monitor = start_lock(parameters, laser)
for _ in range(30):
    feed(lock, parameters, laser, 1)
check('too tight a threshold does block it',
      not parameters.wavemeter_lock_locked.value,
      '(locked anyway, so this test proves nothing)')
check('and says so, with the number to use',
      'waiting for it to settle' in parameters.wavemeter_status.value
      and 'moved' in parameters.wavemeter_status.value,
      '(status: %s)' % parameters.wavemeter_status.value)

parameters = make_parameters()
parameters.wavemeter_skip_line_search.value = True
parameters.wavemeter_settled_within.value = 50.0    # above the wander
laser = FakeLaser(parameters, GHz_per_V=-3.0, offset_GHz=0.04)
laser.wander_GHz = 0.01
lock, control, monitor = start_lock(parameters, laser)
check('a threshold above the wander engages',
      feed_until(lock, parameters, laser,
                 lambda: parameters.gpio_p_out.value == 0b00000000),
      '(status: %s)' % parameters.wavemeter_status.value)

print('with the search off, the handoff window is not what gates it')
# The configuration that stalled in the lab: engage within 50 MHz, but hand
# over to the line search within 5 MHz -- tighter than the laser's own wander,
# so steering could never get there and the lock never engaged. With no line
# search to hand over to, that window has no business gating anything.
parameters = make_parameters()
parameters.wavemeter_skip_line_search.value = True
parameters.wavemeter_range.value = 50.0             # engage within 50 MHz
parameters.wavemeter_handoff_window.value = 5.0     # far tighter
parameters.wavemeter_settled_within.value = 50.0
laser = FakeLaser(parameters, GHz_per_V=-3.0, offset_GHz=0.04)
laser.wander_GHz = 0.01                             # wanders ~20 MHz, no drift
lock, control, monitor = start_lock(parameters, laser)
check('engages on the engage window, not the handoff one',
      feed_until(lock, parameters, laser,
                 lambda: parameters.gpio_p_out.value == 0b00000000),
      '(status: %s)' % parameters.wavemeter_status.value)

print('with the search on, the handoff window still governs')
parameters = make_parameters()
parameters.wavemeter_skip_line_search.value = False
parameters.wavemeter_range.value = 50.0
parameters.wavemeter_handoff_window.value = 5.0
parameters.wavemeter_settled_within.value = 50.0
laser = FakeLaser(parameters, GHz_per_V=-3.0, offset_GHz=0.04)
laser.wander_GHz = 0.01
lock, control, monitor = start_lock(parameters, laser)
for _ in range(25):
    feed(lock, parameters, laser, 1)
check('still steering for the tighter window',
      not parameters.wavemeter_lock_approaching.value)
check('and names the window it is aiming at',
      'steering to within 5.0 MHz' in parameters.wavemeter_status.value
      or 'waiting for it to settle' in parameters.wavemeter_status.value,
      '(status: %s)' % parameters.wavemeter_status.value)

print('a settled laser does hand off')
parameters = make_parameters()
laser = FakeLaser(parameters, GHz_per_V=-3.0, offset_GHz=0.0)
lock, control, monitor = start_lock(parameters, laser)
check('hands over once it sits still',
      feed_until(lock, parameters, laser,
                 lambda: parameters.wavemeter_lock_approaching.value
                 or parameters.wavemeter_lock_confirming.value
                 or parameters.wavemeter_lock_locked.value))
check('waited for repeated confirmation',
      lock.settled_count >= 3 or parameters.wavemeter_lock_locked.value,
      '(settled after %d)' % lock.settled_count)

print('the line search can be switched off entirely')
parameters = make_parameters()
parameters.wavemeter_skip_line_search.value = True
laser = FakeLaser(parameters, GHz_per_V=-3.0, offset_GHz=0.5)
lock, control, monitor = start_lock(parameters, laser)
check('engages once settled, without the line search',
      feed_until(lock, parameters, laser,
                 lambda: parameters.wavemeter_lock_confirming.value))
check('TTL low', parameters.gpio_p_out.value == 0b00000000)
check('the line search never ran',
      lock.approacher is None and not parameters.wavemeter_lock_approaching.value)
check('scan left narrow, not widened to hunt a line',
      parameters.ramp_amplitude.value
      == parameters.wavemeter_steering_ramp_amplitude.value,
      '(got %s)' % parameters.ramp_amplitude.value)
feed(lock, parameters, laser, 5)
check('verified and locked', parameters.wavemeter_lock_locked.value)

print('with the search off, a wrong frequency is still caught')
parameters = make_parameters()
parameters.wavemeter_skip_line_search.value = True
laser = FakeLaser(parameters, GHz_per_V=-3.0, offset_GHz=0.0)
lock, control, monitor = start_lock(parameters, laser)
feed_until(lock, parameters, laser,
           lambda: parameters.wavemeter_lock_confirming.value)
laser.offset_GHz = 3.0
feed(lock, parameters, laser, 5)
check('TTL released', parameters.gpio_p_out.value == 0b11111111)
check('back to steering', parameters.wavemeter_lock_steering.value)

print('relocking keeps the tuning that reached the setpoint')
# The laser starts far from the setpoint, so steering has to move the ramp
# centre a long way. After a drop, that tuning is the best starting point there
# is -- winding it back would put the laser as far away as it began.
parameters = make_parameters()
parameters.wavemeter_skip_line_search.value = True
laser = FakeLaser(parameters, GHz_per_V=-3.0, offset_GHz=1.5)
lock, control, monitor = start_lock(parameters, laser)
start_center = parameters.center.value
check('locks from a long way off',
      feed_until(lock, parameters, laser,
                 lambda: parameters.wavemeter_lock_locked.value),
      '(status: %s)' % parameters.wavemeter_status.value)
steered_center = parameters.center.value
check('steering did move the centre a long way',
      abs(steered_center - start_center) > 0.1,
      '(moved %.3f V)' % (steered_center - start_center))

# now drop the lock
laser.offset_GHz = 2.0
feed(lock, parameters, laser, parameters.wavemeter_max_out_of_range.value + 1)
check('relocked', parameters.wavemeter_lock_steering.value)
# It may have moved by a calibration probe or a correction, but it must be
# near where steering left it rather than back where the lock began.
check('kept the steered centre, did not wind back',
      abs(parameters.center.value - steered_center)
      < abs(parameters.center.value - start_center) / 2,
      '(centre went from %.3f to %.3f, start was %.3f)'
      % (steered_center, parameters.center.value, start_center))

print('stopping does wind the centre back')
parameters = make_parameters()
parameters.wavemeter_skip_line_search.value = True
laser = FakeLaser(parameters, GHz_per_V=-3.0, offset_GHz=1.5)
lock, control, monitor = start_lock(parameters, laser)
start_center = parameters.center.value
feed_until(lock, parameters, laser,
           lambda: parameters.wavemeter_lock_locked.value)
lock.stop()
check('centre restored on stop',
      abs(parameters.center.value - start_center) < 1e-9,
      '(left at %.3f, started at %.3f)'
      % (parameters.center.value, start_center))

print('a lock on the wrong line is released again')
parameters = make_parameters()
laser = FakeLaser(parameters, GHz_per_V=-3.0, offset_GHz=0.0)
lock, control, monitor = start_lock(parameters, laser)
feed_until(lock, parameters, laser,
           lambda: parameters.wavemeter_lock_confirming.value)
check('engaged', parameters.gpio_p_out.value == 0b00000000)

# the lockbox captured, but 3 GHz from where it should have
laser.offset_GHz = 3.0
feed(lock, parameters, laser, 5)
check('TTL released', parameters.gpio_p_out.value == 0b11111111,
      '(still engaged on the wrong line)')
check('back to steering', parameters.wavemeter_lock_steering.value)
check('not reported as locked', not parameters.wavemeter_lock_watching.value)

print('an unverifiable lock is kept, not thrown away')
parameters = make_parameters()
laser = FakeLaser(parameters, GHz_per_V=-3.0, offset_GHz=0.0)
lock, control, monitor = start_lock(parameters, laser)
feed_until(lock, parameters, laser,
           lambda: parameters.wavemeter_lock_confirming.value)
laser.unreachable = True
lock.stage_started_at = lock._now() - 1000   # let the wait time out
feed(lock, parameters, laser, 5)
check('TTL still low', parameters.gpio_p_out.value == 0b00000000)
check('treated as locked and watched',
      parameters.wavemeter_lock_watching.value)
check('flagged as stale', parameters.wavemeter_stale.value)

print('a slow poll does not look like a missing laser')
# The lock looks at the monitor on every acquisition frame but the wavemeter is
# only asked once a second, so most looks repeat the previous answer.
parameters = make_parameters()
laser = FakeLaser(parameters, GHz_per_V=-3.0, offset_GHz=0.0)
control = FakeControl(parameters)
monitor = StubMonitor(laser, parameters, reads_per_poll=6)
lock = WavemeterLock(control, parameters, monitor=monitor,
                     wait_time_between_current_corrections=0)
spectrum = get_signal(parameters.ramp_amplitude.value, 0.0)
middle = len(spectrum) // 2
margin = int(0.01 * len(spectrum))
lock.run(middle - margin, middle + margin, spectrum, auto_offset=False)

check('locks despite repeated answers',
      feed_until(lock, parameters, laser,
                 lambda: parameters.wavemeter_lock_locked.value))
feed(lock, parameters, laser, 60)
check('a laser on setpoint is left alone',
      parameters.gpio_p_out.value == 0b00000000,
      '(relocked: %s)' % parameters.wavemeter_status.value)
check('no out-of-range counted', lock.out_of_range_count == 0)

print('the out-of-range count measures polls, not frames')
laser.offset_GHz = 0.5
allowed = parameters.wavemeter_max_out_of_range.value
# One poll short of the limit, even though that is many frames.
feed(lock, parameters, laser, (allowed - 1) * monitor.reads_per_poll)
check('still locked one poll short',
      parameters.gpio_p_out.value == 0b00000000,
      '(count %d)' % lock.out_of_range_count)
feed(lock, parameters, laser, monitor.reads_per_poll)
check('relocks on the limiting poll',
      parameters.gpio_p_out.value == 0b11111111)

print('the monitor never raises at the caller')
parameters = make_parameters()


def always_broken(*args, **kwargs):
    raise WavemeterError('nope')


monitor = WavemeterMonitor('http://h', SETPOINT, 1.0, reader=always_broken)
check('no arrival before the first poll', monitor.latest()[3] is None)
monitor.poll()
reading, error, reachable, arrival = monitor.latest()
check('error captured', reading is None and error == 'nope' and not reachable)
check('arrival recorded', arrival is not None)
check('arrival stable between polls', monitor.latest()[3] == arrival)
monitor.poll()
check('arrival advances on a new poll', monitor.latest()[3] > arrival)

print()
if failures:
    print('%d check(s) failed: %s' % (len(failures), ', '.join(failures)))
    sys.exit(1)
print('all checks passed')
