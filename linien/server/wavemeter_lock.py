"""Locking against a wavemeter reading.

The three existing modes judge the lock from the error signal itself.  This one
judges it from an absolute frequency: the laser is locked as long as a
wavemeter on the lab network reports it within a window around a setpoint, and
anything else triggers a relock.

A relock runs in two stages, because neither source can do the whole job.  The
wavemeter measures absolute frequency but knows nothing about the spectroscopy
line, so it can only bring the laser near the setpoint; the error signal
resolves the line but cannot tell one line from another several GHz away.  So
the wavemeter steers the ramp centre until the laser is within a handoff window
of the setpoint, and the existing correlation `Approacher` -- the same one the
autolock and relock use -- takes it the rest of the way onto the line.

As in `relock.py`, the PID is not what holds the laser: the lock is engaged and
released through the GPIO TTL that drives the external lockbox, high while
unlocked and scanning, low once locked.  The lockbox does the locking; all it
needs is the TTL.  Two sources still have to concur before the lock is
believed -- the correlation stage has to centre a line and the wavemeter has to
agree the laser is within the engage window -- but the wavemeter is asked
*after* the TTL goes low, not before.

That order matters.  A scanning laser is smeared across the scan as far as the
wavemeter is concerned, so a reading taken while linien is still sweeping
measures the sweep, not the line.  Only once the lockbox is holding the laser
does the wavemeter see a single frequency worth comparing to a setpoint.  So
the lock is engaged first and checked immediately afterwards, and a laser that
turns out to be on the wrong line is released again a second later.
"""

import pickle
import traceback
from time import time

import numpy as np

from linien.common import get_lock_point, combine_error_signal, \
    check_plot_data, SpectrumUncorrelatedException
from linien.server.approach_line import Approacher
from linien.server.wavemeter import WavemeterMonitor

# Steering moves the ramp centre by this fraction of the correction it thinks
# it needs.  Under-correcting costs an iteration or two and keeps a wrong slope
# estimate from throwing the laser further away than it started.
STEERING_GAIN = 0.8

# Never move the centre by more than this in one step, whatever the arithmetic
# says.  A bad slope estimate should waste a step, not lose the laser.
MAX_STEP_V = 0.2

# Probe sizes tried when measuring the centre-to-frequency slope, in volts.
# The first that moves the laser further than CALIBRATION_MIN_SHIFT_MHZ wins.
CALIBRATION_PROBES_V = (0.02, 0.05, 0.1, 0.2)
CALIBRATION_MIN_SHIFT_MHZ = 20.0

# Readings that must land inside the handoff window before the line search
# takes over.  Crossing into the window is not the same as having arrived: a
# laser still moving when the search starts keeps sliding while the search
# zooms, the search walks the ramp centre chasing it, and it leaves the window
# again -- which sends the whole thing back to steering, over and over.  Waiting
# for it to sit still costs a couple of seconds and settles that loop.
HANDOFF_CONFIRMATIONS = 3

# Being inside the window is not enough on its own either.  A laser drifting
# slowly crosses the window over several readings and is inside for all of
# them, while still sliding straight through.  So successive readings also have
# to agree to within this fraction of the window before it counts as settled --
# at the default 200 MHz window, 20 MHz of movement between readings, against
# the ~1 MHz a laser sitting still actually shows.
SETTLED_FRACTION = 0.1

# Give up on a stage rather than steering forever.
STEERING_TIMEOUT_S = 120.0

# What the last look at the wavemeter told us.  These are kept apart because
# they demand opposite responses: the acquisition callback runs far faster than
# the poll interval, so most looks return an answer already acted on, and
# treating that silence as "the laser is missing" would relock a laser that is
# sitting exactly where it should be.
READING_OK = 'ok'                # a new measurement of the laser
READING_ABSENT = 'absent'        # the wavemeter answered; the laser is not there
READING_STALE = 'stale'          # the wavemeter could not be reached
READING_UNCHANGED = 'unchanged'  # no new answer since the last one acted on


class WavemeterLock:
    """Lock kept honest by an absolute frequency reading."""

    def __init__(self, control, parameters, monitor=None,
                 wait_time_between_current_corrections=None):
        self.control = control
        self.parameters = parameters
        self.wait_time_between_current_corrections = \
            wait_time_between_current_corrections

        self.first_error_signal = None
        self.approacher = None
        self.monitor = monitor
        self._data_listener_added = False

        self.parameters.wavemeter_lock_running.value = False
        self.parameters.wavemeter_lock_retrying.value = False
        self.parameters.lock.value = False

        self.reset_properties()

    def reset_properties(self):
        # Each parameter is checked before being set: assigning to one notifies
        # every client, and a fast relock loop would otherwise flood them.
        if self.parameters.wavemeter_lock_failed.value:
            self.parameters.wavemeter_lock_failed.value = False
        if self.parameters.wavemeter_lock_locked.value:
            self.parameters.wavemeter_lock_locked.value = False
        if self.parameters.wavemeter_lock_watching.value:
            self.parameters.wavemeter_lock_watching.value = False
        if self.parameters.wavemeter_lock_confirming.value:
            self.parameters.wavemeter_lock_confirming.value = False

        self.out_of_range_count = 0
        self.settled_count = 0
        self.last_detuning_MHz = None
        self.slope_GHz_per_V = None
        self.calibration_probe_idx = 0
        self.calibration_start = None
        self.stage_started_at = None
        self.last_answer_arrival = None
        self.ignore_readings_before = None

        if self.approacher:
            self.approacher.reset_properties()

    # ------------------------------------------------------------------ setup

    def run(self, x0, x1, spectrum, auto_offset=True):
        """Start the lock from a selected line, as the autolock and relock do."""
        print('RUNNING WAVEMETER LOCK!')
        self.parameters.wavemeter_lock_running.value = True
        self.parameters.fetch_quadratures.value = False
        self.x0, self.x1 = int(x0), int(x1)
        self.auto_offset = auto_offset

        # Unlocked until proven otherwise.
        self.send_lockbox_TTL(locked=False)

        self.record_first_error_signal(spectrum)

        self.initial_ramp_speed = self.parameters.ramp_speed.value
        self.parameters.wavemeter_lock_initial_ramp_amplitude.value = \
            self.parameters.ramp_amplitude.value
        self.initial_ramp_center = self.parameters.center.value

        if self.monitor is None:
            self.monitor = WavemeterMonitor(
                self.parameters.wavemeter_url.value,
                self.parameters.wavemeter_setpoint.value,
                self.parameters.wavemeter_search_range.value,
                use_raw=bool(self.parameters.wavemeter_use_raw.value),
                min_amp=self.parameters.wavemeter_min_amp.value,
                poll_interval=self.parameters.wavemeter_poll_interval.value,
            )
        self.monitor.start()

        self._begin_steering()
        self.add_data_listener()

    def record_first_error_signal(self, error_signal):
        mean_signal, target_slope_rising, calculated_zoom, rolled_error_signal = \
            get_lock_point(error_signal, self.x0, self.x1)

        self.central_y = mean_signal
        self.parameters.target_slope_rising.value = target_slope_rising
        self.control.exposed_write_data()

        self.target_zoom = min(calculated_zoom, 4)
        self.first_error_signal = rolled_error_signal

    def add_data_listener(self):
        if not self._data_listener_added:
            self._data_listener_added = True
            self.parameters.to_plot.on_change(self.react_to_new_spectrum)

    def remove_data_listener(self):
        self._data_listener_added = False
        self.parameters.to_plot.remove_listener(self.react_to_new_spectrum)

    # ------------------------------------------------------------ main loop

    def react_to_new_spectrum(self, plot_data):
        """Driven by acquisition; the wavemeter is read from a cached poll.

        Which stage runs is decided by the wavemeter_lock_* parameters, exactly
        as relock switches on relock_approaching.
        """
        if self.parameters.pause_acquisition.value:
            return

        if plot_data is None or not self.parameters.wavemeter_lock_running.value:
            return

        try:
            if self.parameters.wavemeter_lock_steering.value:
                return self.steer_towards_setpoint()

            if self.parameters.wavemeter_lock_confirming.value:
                return self.verify_lock()

            plot_data = pickle.loads(plot_data)
            if plot_data is None:
                return

            # The PID never engages in this mode -- the lockbox does the
            # locking -- so the signal is always the unlocked one.
            if not check_plot_data(False, plot_data):
                return

            combined_error_signal = combine_error_signal(
                (plot_data['error_signal_1'], plot_data['error_signal_2']),
                self.parameters.dual_channel.value,
                self.parameters.channel_mixing.value,
                self.parameters.combined_offset.value
            )

            if self.parameters.wavemeter_lock_approaching.value:
                return self.approach_line(combined_error_signal)

            return self.watch_lock()

        except SpectrumUncorrelatedException:
            print('Spectrum uncorrelated')
            if self.parameters.watch_wavemeter_lock.value:
                print('retry')
                self.relock('lost the line while approaching')
            else:
                self.fail('error signal no longer matches the reference')

        except Exception:
            traceback.print_exc()
            self.exposed_stop()

    # --------------------------------------------------------- stage: steer

    def _begin_steering(self):
        """Scan narrowly and hand the laser to the wavemeter.

        While the ramp is running the laser sweeps, and the wavemeter reports
        that sweep smeared out.  Narrowing the scan first keeps the reading
        meaningful; the full amplitude is restored before the correlation
        stage, which needs the whole line in view.
        """
        self.parameters.wavemeter_lock_steering.value = True
        self.parameters.wavemeter_lock_approaching.value = False
        self.parameters.wavemeter_lock_confirming.value = False
        self.stage_started_at = self._now()

        self.control.pause_acquisition()
        self.parameters.ramp_amplitude.value = \
            self.parameters.wavemeter_steering_ramp_amplitude.value
        self.control.exposed_write_data()
        self.control.continue_acquisition()

    def steer_towards_setpoint(self):
        outcome, reading, detuning_MHz = self._current_detuning()

        if outcome != READING_OK:
            # Nothing new to steer by. Steering blind is worse than waiting,
            # but waiting forever is not a lock either.
            if self._stage_timed_out():
                return self.fail(self.parameters.wavemeter_status.value
                                 or 'no wavemeter reading while steering')
            return

        if self._stage_timed_out():
            return self.fail('steering did not reach the setpoint in time')

        if self.slope_GHz_per_V is None:
            return self._calibrate_slope(detuning_MHz)

        handoff_MHz = self.parameters.wavemeter_handoff_window.value
        moved_MHz = (None if self.last_detuning_MHz is None
                     else abs(detuning_MHz - self.last_detuning_MHz))
        self.last_detuning_MHz = detuning_MHz

        if abs(detuning_MHz) <= handoff_MHz:
            # Inside the window: stop correcting and watch whether it stays
            # put.  Handing the line search a laser that is still moving is
            # what sends this straight back to steering.
            if moved_MHz is not None and moved_MHz <= handoff_MHz * SETTLED_FRACTION:
                self.settled_count += 1
                if self.settled_count >= HANDOFF_CONFIRMATIONS:
                    return self._begin_approaching()
            else:
                self.settled_count = 0
            return

        self.settled_count = 0
        self._correct_center(detuning_MHz)

    def _calibrate_slope(self, detuning_MHz):
        """Measure GHz per volt of ramp centre by stepping it and looking.

        The sign and size depend on the laser, its current tuning and which
        output drives it, so nothing can be assumed -- but the lock does not
        need a calibration that survives, only one good enough to aim the next
        few steps.
        """
        if self.calibration_start is None:
            self.calibration_start = (self.parameters.center.value, detuning_MHz)
            self._step_center(CALIBRATION_PROBES_V[self.calibration_probe_idx])
            return

        start_center, start_detuning = self.calibration_start
        moved_MHz = detuning_MHz - start_detuning
        probe_V = self.parameters.center.value - start_center

        if abs(moved_MHz) < CALIBRATION_MIN_SHIFT_MHZ:
            # Too small to trust: the laser may just be jittering. Try a bigger
            # probe from where we now are.
            self.calibration_probe_idx += 1
            if self.calibration_probe_idx >= len(CALIBRATION_PROBES_V):
                return self.fail(
                    'laser frequency does not follow the ramp centre '
                    '(moved %.0f MHz for %.2f V)' % (moved_MHz, probe_V)
                )
            self.calibration_start = (self.parameters.center.value, detuning_MHz)
            self._step_center(CALIBRATION_PROBES_V[self.calibration_probe_idx])
            return

        self.slope_GHz_per_V = (moved_MHz * 1e-3) / probe_V
        self.parameters.wavemeter_slope.value = self.slope_GHz_per_V
        print('wavemeter lock: %.3f GHz per volt of ramp centre'
              % self.slope_GHz_per_V)

    def _correct_center(self, detuning_MHz):
        # Detuning is measured - setpoint, so move the centre the other way.
        needed_V = -(detuning_MHz * 1e-3) / self.slope_GHz_per_V
        self._step_center(needed_V * STEERING_GAIN)

    def _step_center(self, step_V):
        step_V = float(np.clip(step_V, -MAX_STEP_V, MAX_STEP_V))
        new_center = self.parameters.center.value + step_V

        if not -1 <= new_center <= 1:
            return self.fail(
                'ramp centre hit the output limit while steering '
                '(wanted %.2f V)' % new_center
            )

        self.control.pause_acquisition()
        self.parameters.center.value = new_center
        self.control.exposed_write_data()
        self.control.continue_acquisition()

        # Judge this step by a reading taken after the laser has settled, not
        # by one already in flight when it was made.
        self.ignore_readings_before = \
            self._now() + self.parameters.wavemeter_settle_time.value

    # ------------------------------------------------------ stage: approach

    def _begin_approaching(self):
        """Hand over to the correlation approacher used by the other modes."""
        print('wavemeter lock: settled at the setpoint, approaching the line')
        self.parameters.wavemeter_lock_steering.value = False
        self.parameters.wavemeter_lock_approaching.value = True
        self.stage_started_at = self._now()

        self.control.pause_acquisition()
        self.parameters.ramp_amplitude.value = \
            self.parameters.wavemeter_lock_initial_ramp_amplitude.value
        self.control.exposed_write_data()
        self.control.continue_acquisition()

        self.approacher = None

    def approach_line(self, combined_error_signal):
        if self.approacher is None:
            self.approacher = Approacher(
                self.control, self.parameters, self.first_error_signal,
                self.target_zoom, self.central_y,
                allow_ramp_speed_change=True,
                wait_time_between_current_corrections=(
                    self.wait_time_between_current_corrections),
                initial_ramp_amplitude=(
                    self.parameters.wavemeter_lock_initial_ramp_amplitude.value)
            )

        if self.approacher.approach_line(combined_error_signal):
            self._lock()

    # ------------------------------------------------------ stage: engage

    def _lock(self):
        self.control.pause_acquisition()

        # Ramp speed does not matter once locked.
        self.parameters.ramp_speed.value = self.initial_ramp_speed
        self.parameters.wavemeter_lock_approaching.value = False
        self.parameters.wavemeter_lock_confirming.value = False

        if self.auto_offset:
            # Only just before locking: doing it while approaching would upset
            # the approacher.
            self.parameters.combined_offset.value = -1 * self.central_y

        self.send_lockbox_TTL(locked=True)
        self.control.continue_acquisition()

        # Engaged, but not yet believed. The lockbox needs a moment to capture
        # the laser, and only once it has is a wavemeter reading worth
        # anything.
        self.parameters.wavemeter_lock_confirming.value = True
        self.out_of_range_count = 0
        self.stage_started_at = self._now()
        self.ignore_readings_before = \
            self._now() + self.parameters.wavemeter_lock_settle_time.value
        print('wavemeter lock: engaged, waiting for the lockbox to settle')

    # ------------------------------------------------------ stage: verify

    def verify_lock(self):
        """Check where the laser actually ended up, now that it is held.

        The correlation stage can centre a line perfectly and still have the
        wrong one -- neighbouring features look alike, which is the whole
        reason for measuring an absolute frequency. This is where that is
        caught, and it is only answerable here: before the lockbox took the
        laser, the wavemeter was watching it sweep.
        """
        outcome, reading, detuning_MHz = self._current_detuning()

        if outcome == READING_OK:
            if abs(detuning_MHz) <= self.parameters.wavemeter_range.value:
                return self._begin_watching()

            reason = ('locked %.1f MHz from the setpoint, outside the %.1f MHz '
                      'window' % (detuning_MHz,
                                  self.parameters.wavemeter_range.value))
            if self.parameters.watch_wavemeter_lock.value:
                return self.relock(reason)
            return self.fail(reason)

        if outcome == READING_ABSENT:
            reason = 'wavemeter cannot see the laser after engaging'
            if self.parameters.watch_wavemeter_lock.value:
                return self.relock(reason)
            return self.fail(reason)

        # No answer yet, or none at all. Waiting costs nothing while the TTL is
        # already low, so wait -- but do not wait forever: an unreachable
        # wavemeter is no reason to throw away a lock that may well be good.
        if self._stage_timed_out():
            print('wavemeter lock: engaged but unverified (%s)'
                  % (self.parameters.wavemeter_status.value or 'no answer'))
            return self._begin_watching()

    def _begin_watching(self):
        self.parameters.wavemeter_lock_confirming.value = False
        self.parameters.wavemeter_lock_locked.value = True
        self.parameters.wavemeter_lock_watching.value = True
        self.parameters.wavemeter_lock_retrying.value = False
        self.out_of_range_count = 0
        self.stage_started_at = self._now()
        print('wavemeter lock: locked')

    # -------------------------------------------------------- stage: watch

    def watch_lock(self):
        """Locked: relock when the frequency leaves the window.

        Only a new answer is judged, so the out-of-range count measures polls
        rather than acquisition frames -- five means five wavemeter readings,
        not five turns of a loop that spins much faster than the wavemeter is
        asked anything.
        """
        outcome, reading, detuning_MHz = self._current_detuning()

        if outcome == READING_UNCHANGED:
            # Nothing has happened since the last judgement. Silence is not
            # evidence either way.
            return

        if outcome == READING_STALE:
            # A wavemeter that cannot be reached says nothing about the laser,
            # so hold the lock and flag it rather than relocking on what may
            # well be a network problem.
            return

        if outcome == READING_ABSENT:
            # The wavemeter answered and does not see the laser anywhere in the
            # search window. That is a statement about the laser, not the link.
            return self._count_out_of_range('laser not found by the wavemeter')

        if abs(detuning_MHz) > self.parameters.wavemeter_range.value:
            return self._count_out_of_range(
                'off setpoint by %.1f MHz' % detuning_MHz
            )

        self.out_of_range_count = 0

    def _count_out_of_range(self, reason):
        self.out_of_range_count += 1
        allowed = self.parameters.wavemeter_max_out_of_range.value
        print('wavemeter lock: %s (%d/%d)'
              % (reason, self.out_of_range_count, allowed))

        if self.out_of_range_count >= allowed:
            if self.parameters.watch_wavemeter_lock.value:
                self.relock(reason)
            else:
                self.fail(reason)

    # ------------------------------------------------------------- control

    def relock(self, reason):
        print('wavemeter lock: relocking (%s)' % reason)
        # _reset_scan takes the TTL back high, releasing the lockbox if the
        # relock was decided after engaging.

        if not self.parameters.wavemeter_lock_running.value:
            self.parameters.wavemeter_lock_running.value = True
        if not self.parameters.wavemeter_lock_retrying.value:
            self.parameters.wavemeter_lock_retrying.value = True

        self.reset_properties()
        self._reset_scan()
        self._begin_steering()
        self.add_data_listener()

    def fail(self, reason):
        print('wavemeter lock failed: %s' % reason)
        self.parameters.wavemeter_status.value = 'failed: ' + reason
        self.exposed_stop()
        self.parameters.wavemeter_lock_failed.value = True

    def exposed_stop(self):
        """Abort any operation."""
        if self.monitor is not None:
            self.monitor.stop()

        self.parameters.wavemeter_lock_running.value = False
        self.parameters.wavemeter_lock_locked.value = False
        self.parameters.wavemeter_lock_steering.value = False
        self.parameters.wavemeter_lock_approaching.value = False
        self.parameters.wavemeter_lock_confirming.value = False
        self.parameters.wavemeter_lock_watching.value = False
        self.parameters.fetch_quadratures.value = True
        self.remove_data_listener()

        self._reset_scan()
        self.parameters.task.value = None

    # `stop` is what lock_status_panel calls on the running task.
    stop = exposed_stop

    def _reset_scan(self):
        self.control.pause_acquisition()

        self.send_lockbox_TTL(locked=False)

        self.parameters.center.value = self.initial_ramp_center
        self.parameters.ramp_amplitude.value = \
            self.parameters.wavemeter_lock_initial_ramp_amplitude.value
        self.parameters.ramp_speed.value = self.initial_ramp_speed
        self.control.exposed_start_ramp()

        self.control.continue_acquisition()

    def send_lockbox_TTL(self, locked=False):
        """GPIO low once locked, high while unlocked -- as in relock.py."""
        value = 0b00000000 if locked else 0b11111111
        self.parameters.gpio_p_out.value = value
        self.parameters.gpio_n_out.value = value
        self.control.exposed_write_data()

    # ------------------------------------------------------------- helpers

    def _now(self):
        return time()

    def _stage_timed_out(self):
        if self.stage_started_at is None:
            return False
        return self._now() - self.stage_started_at > STEERING_TIMEOUT_S

    def _current_detuning(self):
        """The newest wavemeter answer not yet acted on, published to the GUI.

        Returns (outcome, reading, detuning_MHz).  Every answer is consumed at
        most once: the poller stores when each arrived, and an arrival already
        seen comes back as READING_UNCHANGED.  Without that, a stage running at
        the acquisition rate would act many times on one measurement -- five
        "readings" out of range would elapse in well under a second, and a step
        would be judged by a reading taken before it was made.
        """
        reading, error, reachable, arrival = self.monitor.latest()

        if arrival is None:
            # Nothing fetched yet at all.
            self._set_stale('no answer from the wavemeter yet')
            return READING_UNCHANGED, None, None

        if arrival == self.last_answer_arrival:
            return READING_UNCHANGED, None, None

        if error is not None or not reachable:
            self.last_answer_arrival = arrival
            self._set_stale(error or 'no answer from the wavemeter')
            return READING_STALE, None, None

        if reading is None:
            self.last_answer_arrival = arrival
            self.parameters.wavemeter_stale.value = False
            self.parameters.wavemeter_status.value = \
                'wavemeter sees no laser within %.3f GHz of the setpoint' \
                % self.parameters.wavemeter_search_range.value
            return READING_ABSENT, None, None

        # A steering step is judged by a reading that arrived after the laser
        # had time to settle, not by one already in flight when it was made.
        # Freshness is decided on the local clock: the timestamp inside the
        # reading comes from the wavemeter PC, which is not synchronised.
        if self.ignore_readings_before is not None:
            if arrival < self.ignore_readings_before:
                return READING_UNCHANGED, None, None
            self.ignore_readings_before = None

        self.last_answer_arrival = arrival
        detuning_MHz = float(reading['detuning_MHz'])

        self.parameters.wavemeter_stale.value = False
        self.parameters.wavemeter_frequency.value = float(
            reading.get('raw_median_GHz' if self.parameters.wavemeter_use_raw.value
                        else 'median_GHz', 0.0)
        )
        self.parameters.wavemeter_detuning.value = detuning_MHz
        self.parameters.wavemeter_age.value = float(reading.get('age_s', 0.0))
        self.parameters.wavemeter_status.value = \
            '%.1f MHz from setpoint' % detuning_MHz

        return READING_OK, reading, detuning_MHz

    def _set_stale(self, message):
        if not self.parameters.wavemeter_stale.value:
            self.parameters.wavemeter_stale.value = True
        self.parameters.wavemeter_status.value = 'holding: ' + message
