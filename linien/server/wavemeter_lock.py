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
unlocked and scanning, low once locked.
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

# Give up on a stage rather than steering forever.
STEERING_TIMEOUT_S = 120.0


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

        self.out_of_range_count = 0
        self.slope_GHz_per_V = None
        self.calibration_probe_idx = 0
        self.calibration_start = None
        self.stage_started_at = None
        self.last_reading_time = None
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
        self.stage_started_at = self._now()

        self.control.pause_acquisition()
        self.parameters.ramp_amplitude.value = \
            self.parameters.wavemeter_steering_ramp_amplitude.value
        self.control.exposed_write_data()
        self.control.continue_acquisition()

    def steer_towards_setpoint(self):
        reading, detuning_MHz = self._current_detuning()

        if reading is None:
            # Nothing to steer by. Steering blind is worse than waiting, but
            # waiting forever is not a lock either.
            if self._stage_timed_out():
                return self.fail(self.parameters.wavemeter_status.value
                                 or 'no wavemeter reading while steering')
            return

        if self._stage_timed_out():
            return self.fail('steering did not reach the setpoint in time')

        if self.slope_GHz_per_V is None:
            return self._calibrate_slope(detuning_MHz)

        handoff_MHz = self.parameters.wavemeter_handoff_window.value * 1e3
        if abs(detuning_MHz) <= handoff_MHz:
            return self._begin_approaching()

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
        print('wavemeter lock: at the setpoint, approaching the line')
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

    def _lock(self):
        self.control.pause_acquisition()

        # Ramp speed does not matter once locked.
        self.parameters.ramp_speed.value = self.initial_ramp_speed
        self.parameters.wavemeter_lock_approaching.value = False

        if self.auto_offset:
            # Only just before locking: doing it while approaching would upset
            # the approacher.
            self.parameters.combined_offset.value = -1 * self.central_y

        self.send_lockbox_TTL(locked=True)
        self.control.continue_acquisition()

        self.parameters.wavemeter_lock_locked.value = True
        self.parameters.wavemeter_lock_watching.value = True
        self.parameters.wavemeter_lock_retrying.value = False
        self.out_of_range_count = 0
        self.stage_started_at = self._now()
        print('wavemeter lock: locked')

    # -------------------------------------------------------- stage: watch

    def watch_lock(self):
        """Locked: relock when the frequency leaves the window.

        A wavemeter that cannot be reached is not evidence about the laser, so
        the lock is held and the condition flagged instead of relocking on what
        may well be a network problem.
        """
        reading, detuning_MHz = self._current_detuning()

        if reading is None and self.parameters.wavemeter_stale.value:
            # Held, not locked-out: leave the TTL where it is and wait.
            return

        if reading is None:
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
        """Latest wavemeter answer, published to the GUI on the way past.

        Returns (reading, detuning_MHz); reading is None when there is no
        usable measurement, and `wavemeter_stale` distinguishes "could not ask"
        from "asked, and the laser is not there".
        """
        reading, error, reachable, age = self.monitor.latest()

        if error is not None or not reachable:
            self._set_stale(error or 'no answer from the wavemeter yet')
            return None, None

        if reading is None:
            self.parameters.wavemeter_stale.value = False
            self.parameters.wavemeter_status.value = \
                'wavemeter sees no laser within %.3f GHz of the setpoint' \
                % self.parameters.wavemeter_search_range.value
            return None, None

        # One poll answers many acquisition frames; only act on a fresh one so
        # that a steering step is judged by a reading taken after it.  Freshness
        # is decided on the local clock -- the timestamp in the reading comes
        # from the wavemeter PC and the two are not synchronised.
        arrival = self._now() - (age or 0.0)
        if self.ignore_readings_before is not None:
            if arrival < self.ignore_readings_before:
                return None, None
            self.ignore_readings_before = None

        reading_time = reading.get('time')
        if reading_time is not None and reading_time == self.last_reading_time:
            return None, None
        self.last_reading_time = reading_time

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

        return reading, detuning_MHz

    def _set_stale(self, message):
        if not self.parameters.wavemeter_stale.value:
            self.parameters.wavemeter_stale.value = True
        self.parameters.wavemeter_status.value = 'holding: ' + message
