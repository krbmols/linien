from PyQt5 import QtGui, QtWidgets
from linien.gui.widgets import CustomWidget
from linien.gui.utils_gui import param2ui, server_has_wavemeter_lock
import time

class LockingPanel(QtWidgets.QWidget, CustomWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.load_ui('locking_panel.ui')
        # Set for real in connection_established, once the server has said
        # which parameters it has.  Assume not until then: the widgets exist
        # regardless of what the Red Pitaya is running.
        self.has_wavemeter = False

    def ready(self):
        # PID controls
        self.ids.kp.setKeyboardTracking(False)
        self.ids.kp.valueChanged.connect(self.kp_changed)
        self.ids.ki.setKeyboardTracking(False)
        self.ids.ki.valueChanged.connect(self.ki_changed)
        self.ids.kd.setKeyboardTracking(False)
        self.ids.kd.valueChanged.connect(self.kd_changed)

        # Check which tab we are in
        self.ids.lock_control_container.currentChanged.connect(self.lock_mode_changed)

        # Autolock tab checkboxes
        self.ids.checkLockCheckbox.stateChanged.connect(self.check_lock_changed)
        self.ids.watchLockCheckbox.stateChanged.connect(self.watch_lock_changed)
        self.ids.watch_lock_threshold.valueChanged.connect(self.watch_lock_threshold_changed)
        self.ids.autoOffsetCheckbox.stateChanged.connect(self.auto_offset_changed)

        # Parameters from actually clicking on the line using autolock
        self.ids.selectLineToLock.clicked.connect(self.start_autolock_selection)
        self.ids.abortLineSelection.clicked.connect(self.stop_autolock_selection)

        # Relock tab checkboxes
        self.ids.watchLockCheckbox_relock.stateChanged.connect(self.watch_relock_changed)
        self.ids.autoOffsetCheckbox_relock.stateChanged.connect(self.relock_auto_offset_changed)
        self.ids.STDthresholdbox_relock.valueChanged.connect(self.std_relock_threshold_changed)

        # Parameters from actually clicking on the line using relock
        self.ids.selectLineToRelock.clicked.connect(self.start_relock_selection)
        self.ids.abortRelockSelection.clicked.connect(self.stop_relock_selection)

        # Wavemeter lock tab
        self.ids.selectLineToWavemeterLock.clicked.connect(
            self.start_wavemeter_lock_selection)
        self.ids.abortWavemeterSelection.clicked.connect(
            self.stop_wavemeter_lock_selection)
        self.ids.wavemeter_test_button.clicked.connect(self.test_wavemeter)

        self.ids.wavemeter_url.editingFinished.connect(self.wavemeter_url_changed)
        for name in ('wavemeter_setpoint', 'wavemeter_range',
                     'wavemeter_search_range', 'wavemeter_handoff_window',
                     'wavemeter_min_amp', 'wavemeter_poll_interval',
                     'wavemeter_max_out_of_range',
                     'wavemeter_steering_ramp_amplitude',
                     'wavemeter_settled_within', 'wavemeter_settle_time',
                     'wavemeter_lock_settle_time'):
            element = getattr(self.ids, name)
            element.setKeyboardTracking(False)
            element.valueChanged.connect(
                lambda value, name=name: self.wavemeter_value_changed(name, value)
            )

        self.ids.wavemeter_skip_line_search_checkbox.stateChanged.connect(
            self.wavemeter_skip_line_search_changed)
        self.ids.wavemeter_use_raw_checkbox.stateChanged.connect(
            self.wavemeter_use_raw_changed)
        self.ids.watch_wavemeter_checkbox.stateChanged.connect(
            self.watch_wavemeter_changed)
        self.ids.autoOffsetCheckbox_wavemeter.stateChanged.connect(
            self.wavemeter_auto_offset_changed)

        # Manual lock
        self.ids.manualLockButton.clicked.connect(self.start_manual_lock)

        self.ids.pid_on_slow_strength.setKeyboardTracking(False)
        self.ids.pid_on_slow_strength.valueChanged.connect(self.pid_on_slow_strength_changed)

        self.ids.reset_lock_failed_state.clicked.connect(self.reset_lock_failed)

    def connection_established(self):
        # Initialization
        params = self.app().parameters
        self.parameters = params
        self.control = self.app().control

        # GPIO parameters
        def gpio_update(*_):
            gpo = "GPIO P OUT: " + str(params.gpio_p_out.value)
            gno = "GPIO N OUT: " + str(params.gpio_n_out.value)
            self.ids.gpio_p_label.setText(gpo)
            self.ids.gpio_n_label.setText(gno)
        
        params.gpio_p_out.on_change(gpio_update)
        params.gpio_n_out.on_change(gpio_update)

        # error STD update
        std_update_time = 0
        def std_update(*_):
            nonlocal std_update_time
            now = time.time()
            if now - std_update_time < 0.5:  # only update every 0.5 s
                return
            std_update_time = now
            stdtext = "STD of error signal: " + str(params.relock_std_val.value)
            self.ids.std_display.setText(stdtext)
        
        params.relock_std_val.on_change(std_update)
            
            
        # PID parameters
        param2ui(params.p, self.ids.kp)
        param2ui(params.i, self.ids.ki)
        param2ui(params.d, self.ids.kd)

        # Autolock parameters
        param2ui(params.check_lock, self.ids.checkLockCheckbox)
        param2ui(params.watch_lock, self.ids.watchLockCheckbox)
        param2ui(
            params.watch_lock_threshold,
            self.ids.watch_lock_threshold,
            lambda v: v * 100
        )
        param2ui(params.autolock_determine_offset, self.ids.autoOffsetCheckbox)

        # Relock parameters:
        param2ui(params.watch_relock, self.ids.watchLockCheckbox_relock)
        param2ui(
            params.watch_relock_threshold,
            self.ids.STDthresholdbox_relock,
            lambda v: v
        )
        param2ui(params.relock_determine_offset, self.ids.autoOffsetCheckbox_relock)

        # An out-of-date Red Pitaya exposes none of the wavemeter parameters.
        # Say so plainly and leave the other three modes working, rather than
        # failing with an AttributeError that names neither cause nor cure.
        self.has_wavemeter = server_has_wavemeter_lock(params)

        if not self.has_wavemeter:
            container = self.ids.lock_control_container
            index = container.indexOf(self.ids.wavemeter_mode)
            if index != -1:
                container.setTabEnabled(index, False)
                container.setTabToolTip(
                    index,
                    'The Red Pitaya is running an older server without the '
                    'wavemeter lock. Deploy it with install_relock_server.'
                )
            self.ids.wavemeter_reading_label.setStyleSheet('color: #d40000')
            self.ids.wavemeter_reading_label.setText(
                'Red Pitaya server is out of date -- run install_relock_server '
                'to deploy the wavemeter lock.'
            )
        else:
            # Wavemeter lock parameters
            for name in ('wavemeter_url', 'wavemeter_setpoint', 'wavemeter_range',
                         'wavemeter_search_range', 'wavemeter_handoff_window',
                         'wavemeter_min_amp', 'wavemeter_poll_interval',
                         'wavemeter_max_out_of_range',
                         'wavemeter_steering_ramp_amplitude',
                         'wavemeter_settled_within',
                         'wavemeter_settle_time',
                         'wavemeter_lock_settle_time'):
                param2ui(getattr(params, name), getattr(self.ids, name))

            param2ui(params.wavemeter_skip_line_search,
                     self.ids.wavemeter_skip_line_search_checkbox)
            param2ui(params.wavemeter_use_raw, self.ids.wavemeter_use_raw_checkbox)
            param2ui(params.watch_wavemeter_lock, self.ids.watch_wavemeter_checkbox)
            param2ui(params.wavemeter_lock_determine_offset,
                     self.ids.autoOffsetCheckbox_wavemeter)

            def wavemeter_reading_changed(*_):
                if params.wavemeter_stale.value:
                    colour = '#d40000'
                elif abs(params.wavemeter_detuning.value) <= params.wavemeter_range.value:
                    colour = '#00aa00'
                else:
                    colour = '#d47500'

                status = params.wavemeter_status.value or 'not polled yet'
                self.ids.wavemeter_reading_label.setStyleSheet('color: ' + colour)
                self.ids.wavemeter_reading_label.setText(
                    '%.3f GHz  \u2014  %s' % (params.wavemeter_frequency.value, status)
                )

            for param in (params.wavemeter_frequency, params.wavemeter_detuning,
                          params.wavemeter_status, params.wavemeter_stale):
                param.on_change(wavemeter_reading_changed)

            def wavemeter_selection_status_changed(value):
                self.ids.wavemeter_activated.setVisible(value)
                self.ids.wavemeter_not_activated.setVisible(not value)
            params.wavemeter_lock_selection.on_change(
                wavemeter_selection_status_changed)

        # Handle the tab
        def _sync_tab_from_params(*_):
            # Decide the index from parameters
            if self.parameters.automatic_mode.value:
                idx = 0  # Auto
            elif self.parameters.relock_automatic_mode.value:
                idx = 1  # Relock
            elif self.has_wavemeter \
                    and self.parameters.wavemeter_lock_automatic_mode.value:
                idx = 2  # Wavemeter
            else:
                idx = 3  # Manual

            # Avoid re-entrant fights by only updating when different
            if self.ids.lock_control_container.currentIndex() != idx:
                self.ids.lock_control_container.setCurrentIndex(idx)

        # Subscribe to param changes
        self.parameters.automatic_mode.on_change(_sync_tab_from_params)
        self.parameters.relock_automatic_mode.on_change(_sync_tab_from_params)
        if self.has_wavemeter:
            self.parameters.wavemeter_lock_automatic_mode.on_change(
                _sync_tab_from_params)

        #Slow PID
        param2ui(params.pid_on_slow_strength, self.ids.pid_on_slow_strength)
        def slow_pid_visibility(*args):
            self.ids.slow_pid_group.setVisible(self.parameters.pid_on_slow_enabled.value)
        params.pid_on_slow_enabled.on_change(slow_pid_visibility)

        def lock_status_changed(_):
            locked = params.lock.value
            task = params.task.value
            al_failed = params.autolock_failed.value
            rl_failed = params.relock_failed.value
            wm_failed = self.has_wavemeter and params.wavemeter_lock_failed.value
            task_running = (task is not None) and (not al_failed) \
                and (not rl_failed) and (not wm_failed)

            if locked or task_running:
                self.ids.lock_control_container.hide()
            else:
                self.ids.lock_control_container.show()

            self.ids.lock_failed.setVisible(al_failed or rl_failed or wm_failed)

        for param in (params.lock, params.autolock_approaching, params.autolock_watching,
                      params.autolock_failed, params.autolock_locked, 
                      params.relock_approaching, params.relock_watching,
                      params.relock_failed, params.relock_locked):
            param.on_change(lock_status_changed)

        if self.has_wavemeter:
            for param in (params.wavemeter_lock_steering,
                          params.wavemeter_lock_approaching,
                          params.wavemeter_lock_watching,
                          params.wavemeter_lock_failed,
                          params.wavemeter_lock_locked):
                param.on_change(lock_status_changed)

        param2ui(params.target_slope_rising, self.ids.button_slope_rising)
        param2ui(
            params.target_slope_rising,
            self.ids.button_slope_falling,
            lambda value: not value
        )

        def autolock_selection_status_changed(value):
            self.ids.auto_mode_activated.setVisible(value)
            self.ids.auto_mode_not_activated.setVisible(not value)
        params.autolock_selection.on_change(autolock_selection_status_changed)

        def relock_selection_status_changed(value):
            self.ids.relock_activated.setVisible(value)
            self.ids.relock_not_activated.setVisible(not value)
        params.relock_selection.on_change(relock_selection_status_changed)
    
    # PID tab interactions
    def kp_changed(self):
        self.parameters.p.value = self.ids.kp.value()
        self.control.write_data()

    def ki_changed(self):
        self.parameters.i.value = self.ids.ki.value()
        self.control.write_data()

    def kd_changed(self):
        self.parameters.d.value = self.ids.kd.value()
        self.control.write_data()

    def lock_mode_changed(self, idx):
        self.parameters.automatic_mode.value = idx == 0
        self.parameters.relock_automatic_mode.value = idx == 1
        if self.has_wavemeter:
            self.parameters.wavemeter_lock_automatic_mode.value = idx == 2

    def start_manual_lock(self):
        self.control.pause_acquisition()
        self.parameters.target_slope_rising.value = self.ids.button_slope_rising.isChecked()
        self.parameters.fetch_quadratures.value = False
        self.control.write_data()
        self.control.start_lock()
        self.control.continue_acquisition()

    # Autolock tab interactions
    def check_lock_changed(self):
        self.parameters.check_lock.value = int(self.ids.checkLockCheckbox.checkState())

    def watch_lock_changed(self):
        self.parameters.watch_lock.value = int(self.ids.watchLockCheckbox.checkState())

    def auto_offset_changed(self):
        self.parameters.autolock_determine_offset.value = int(self.ids.autoOffsetCheckbox.checkState())

    def pid_on_slow_strength_changed(self):
        self.parameters.pid_on_slow_strength.value = self.ids.pid_on_slow_strength.value()
        self.control.write_data()

    def start_autolock_selection(self):
        self.parameters.autolock_selection.value = True

    def stop_autolock_selection(self):
        self.parameters.autolock_selection.value = False

    def watch_lock_threshold_changed(self):
        self.parameters.watch_lock_threshold.value = self.ids.watch_lock_threshold.value() / 100.0

    def reset_lock_failed(self):
        self.parameters.autolock_failed.value = False
        self.parameters.relock_failed.value = False
        if self.has_wavemeter:
            self.parameters.wavemeter_lock_failed.value = False

    # Relock tab interactions
    def watch_relock_changed(self):
        self.parameters.watch_relock.value = int(self.ids.watchLockCheckbox_relock.checkState())

    def relock_auto_offset_changed(self):
        self.parameters.relock_determine_offset.value = int(self.ids.autoOffsetCheckbox_relock.checkState())

    def start_relock_selection(self):
        self.parameters.relock_selection.value = True

    def stop_relock_selection(self):
        self.parameters.relock_selection.value = False

    def std_relock_threshold_changed(self):
        self.parameters.watch_relock_threshold.value = self.ids.STDthresholdbox_relock.value()

    # Wavemeter lock tab interactions
    def wavemeter_value_changed(self, name, value):
        if not self.has_wavemeter:
            return
        getattr(self.parameters, name).value = value

    def wavemeter_url_changed(self):
        if not self.has_wavemeter:
            return
        self.parameters.wavemeter_url.value = self.ids.wavemeter_url.text().strip()

    def wavemeter_skip_line_search_changed(self):
        if not self.has_wavemeter:
            return
        self.parameters.wavemeter_skip_line_search.value = \
            bool(self.ids.wavemeter_skip_line_search_checkbox.checkState())

    def wavemeter_use_raw_changed(self):
        if not self.has_wavemeter:
            return
        self.parameters.wavemeter_use_raw.value = \
            bool(self.ids.wavemeter_use_raw_checkbox.checkState())

    def watch_wavemeter_changed(self):
        if not self.has_wavemeter:
            return
        self.parameters.watch_wavemeter_lock.value = \
            bool(self.ids.watch_wavemeter_checkbox.checkState())

    def wavemeter_auto_offset_changed(self):
        if not self.has_wavemeter:
            return
        self.parameters.wavemeter_lock_determine_offset.value = \
            int(self.ids.autoOffsetCheckbox_wavemeter.checkState())

    def start_wavemeter_lock_selection(self):
        if not self.has_wavemeter:
            return
        self.parameters.wavemeter_lock_selection.value = True

    def stop_wavemeter_lock_selection(self):
        if not self.has_wavemeter:
            return
        self.parameters.wavemeter_lock_selection.value = False

    def test_wavemeter(self):
        """Read the wavemeter once, from the GUI machine, and show the answer.

        This is only a reachability check for the address as typed. The lock
        itself polls from the RedPitaya, which may not be able to reach a host
        this machine can.
        """
        from linien.server.wavemeter import read_once, WavemeterError

        self.ids.wavemeter_reading_label.setStyleSheet('color: #888a85')
        self.ids.wavemeter_reading_label.setText('testing...')
        QtWidgets.QApplication.processEvents()

        try:
            reading = read_once(
                self.ids.wavemeter_url.text(),
                self.ids.wavemeter_setpoint.value(),
                self.ids.wavemeter_search_range.value(),
                use_raw=bool(self.ids.wavemeter_use_raw_checkbox.checkState()),
                min_amp=self.ids.wavemeter_min_amp.value()
            )
        except WavemeterError as error:
            self.ids.wavemeter_reading_label.setStyleSheet('color: #d40000')
            self.ids.wavemeter_reading_label.setText(str(error))
            return

        if reading is None:
            self.ids.wavemeter_reading_label.setStyleSheet('color: #d47500')
            self.ids.wavemeter_reading_label.setText(
                'wavemeter reached, but it sees no laser within %.3f GHz of '
                'the setpoint' % self.ids.wavemeter_search_range.value()
            )
            return

        key = 'raw_median_GHz' \
            if self.ids.wavemeter_use_raw_checkbox.checkState() else 'median_GHz'
        self.ids.wavemeter_reading_label.setStyleSheet('color: #00aa00')
        self.ids.wavemeter_reading_label.setText(
            '%.3f GHz, %.1f MHz from setpoint (%s)'
            % (reading.get(key, 0.0), reading['detuning_MHz'],
               reading.get('used', 'unknown'))
        )