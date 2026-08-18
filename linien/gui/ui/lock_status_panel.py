import numpy as np
from PyQt5 import QtGui, QtWidgets, QtWidgets
from PyQt5.QtCore import QTimer
from linien.gui.widgets import CustomWidget
from linien.gui.utils_gui import param2ui


class LockStatusPanel(QtWidgets.QWidget, CustomWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Add flag to track if TTL is currently active
        self.ttl_active = False

    def ready(self):
        self.ids.stop_lock.clicked.connect(self.stop_lock)
        self.ids.control_signal_history_length.setKeyboardTracking(False)
        self.ids.control_signal_history_length.valueChanged.connect(self.control_signal_history_length_changed)
        
        # Connect to the application's aboutToQuit signal to turn off TTL on shutdown
        app = QtWidgets.QApplication.instance()
        app.aboutToQuit.connect(self.turn_off_ttl_on_shutdown)

    def connection_established(self):
        self.control = self.app().control
        params = self.app().parameters
        self.parameters = params

        def update_status(_):
            locked = params.lock.value
            task = params.task.value
            al_failed = params.autolock_failed.value
            al_running = params.autolock_running.value
            al_retrying = params.autolock_retrying.value
            rl_failed = params.relock_failed.value
            rl_running = params.relock_running.value
            rl_retrying = params.relock_retrying.value
            wm_failed = params.wavemeter_lock_failed.value
            wm_running = params.wavemeter_lock_running.value
            wm_retrying = params.wavemeter_lock_retrying.value

            if locked or (task is not None and not al_failed and not rl_failed
                          and not wm_failed):
                self.show()
                
                # REVERSED LOGIC: Deactivate TTL when locked and TTL is active
                if locked and self.ttl_active:
                    self.restore_gpio_output()
            else:
                self.hide()
                
                # REVERSED LOGIC: Activate TTL when unlocked and TTL is not active
                if not locked and not self.ttl_active:
                    self.send_ttl_signal()

            explain = not al_running #NO clue what this does

            if task:
                al_watching = params.autolock_watching.value
                rl_watching = params.relock_watching.value
                wm_watching = params.wavemeter_lock_watching.value
                wm_steering = params.wavemeter_lock_steering.value
            else:
                al_running = False
                al_watching = False
                rl_running = False
                rl_watching = False
                wm_running = False
                wm_watching = False
                wm_steering = False

            def set_text(text):
                self.ids.lock_status.setText(text)

            if not al_running and locked:
                set_text('Manually Locked!')
            if al_running and al_watching:
                set_text('(Auto)Locked! Watching continuously...')
            if al_running and not al_watching:
                if not al_retrying:
                    set_text('Autolock is running...')
                else:
                    set_text('Trying again to autolock...')

            if rl_running and rl_watching:
                set_text('(Re)Locked! Watching continuously...')
            if rl_running and not rl_watching:
                if not rl_retrying:
                    set_text('Relock is running...')
                else:
                    set_text('Trying again to relock...')

            if wm_running:
                detuning = params.wavemeter_detuning.value
                if params.wavemeter_stale.value:
                    # Holding the lock because the wavemeter went quiet, not
                    # because the laser did anything.
                    set_text('Wavemeter unreachable -- holding lock')
                elif wm_watching:
                    set_text('Locked to %.3f GHz! %+.1f MHz from setpoint'
                             % (params.wavemeter_frequency.value, detuning))
                elif wm_steering:
                    set_text('Steering onto the setpoint (%+.1f MHz to go)...'
                             % detuning)
                elif wm_retrying:
                    set_text('Trying again to reach the setpoint...')
                else:
                    set_text('Approaching the line...')

        for param in (params.lock, params.autolock_approaching, params.autolock_watching,
                params.autolock_failed, params.autolock_locked, params.autolock_retrying,
                params.relock_approaching, params.relock_watching, params.relock_failed, 
                params.relock_locked, params.relock_retrying,
                params.wavemeter_lock_steering, params.wavemeter_lock_approaching,
                params.wavemeter_lock_watching, params.wavemeter_lock_failed,
                params.wavemeter_lock_locked, params.wavemeter_lock_retrying,
                params.wavemeter_detuning, params.wavemeter_stale):
            param.on_change(update_status)
            
        # Add callback for lock status
        # self.parameters.lock.on_change(self.on_lock_status_changed)

        param2ui(
            params.control_signal_history_length,
            self.ids.control_signal_history_length
        )
    
    # def on_lock_status_changed(self, locked):
    #     # REVERSED LOGIC: Deactivate TTL when locked, activate when unlocked
    #     if locked:
    #         # Restore original GPIO values when locked
    #         if hasattr(self, 'original_gpio_p') and hasattr(self, 'original_gpio_n'):
    #             self.parameters.gpio_p_out.value = self.original_gpio_p
    #             self.parameters.gpio_n_out.value = self.original_gpio_n
    #             self.control.write_data()
    #             self.ttl_active = False
    #     else:
    #         # Store original GPIO values if not already stored
    #         if not hasattr(self, 'original_gpio_p'):
    #             self.original_gpio_p = self.parameters.gpio_p_out.value
    #             self.original_gpio_n = self.parameters.gpio_n_out.value
            
    #         # Set all GPIO pins high (both P and N) when unlocked
    #         self.parameters.gpio_p_out.value = 0b11111111
    #         self.parameters.gpio_n_out.value = 0b11111111
    #         self.control.write_data()
    #         self.ttl_active = True

    def stop_lock(self):
        self.parameters.fetch_quadratures.value = True

        if self.parameters.task.value is not None:
            self.parameters.task.value.stop()
            self.parameters.task.value = None
        else:
            self.control.exposed_start_ramp()
            
        # REVERSED LOGIC: Activate TTL when stopping the lock (since lock will be off)
        if not self.ttl_active:
            self.send_ttl_signal()

    def control_signal_history_length_changed(self):
        self.parameters.control_signal_history_length.value = \
            self.ids.control_signal_history_length.value()
            
    def send_ttl_signal(self):
        """Send TTL signal by setting all GPIO pins high"""
        # Store original GPIO values if not already stored
        if not hasattr(self, 'original_gpio_p'):
            self.original_gpio_p = self.parameters.gpio_p_out.value
            self.original_gpio_n = self.parameters.gpio_n_out.value
        
        # Set all GPIO pins high (both P and N)
        self.parameters.gpio_p_out.value = 0b11111111  # All 8 P pins high
        self.parameters.gpio_n_out.value = 0b11111111  # All 8 N pins high
        self.control.write_data()
        
        # Update TTL active flag
        self.ttl_active = True
        
    def restore_gpio_output(self):
        """Restore GPIO pins to their original values"""
        if hasattr(self, 'original_gpio_p') and hasattr(self, 'original_gpio_n'):
            self.parameters.gpio_p_out.value = self.original_gpio_p
            self.parameters.gpio_n_out.value = self.original_gpio_n
            self.control.write_data()
            
            # Update TTL active flag
            self.ttl_active = False
            
    def turn_off_ttl_on_shutdown(self):
        """Turn off all GPIO pins (set to 0) when the application shuts down"""
        try:
            # Set all GPIO pins to 0 (both P and N)
            self.parameters.gpio_p_out.value = 0
            self.parameters.gpio_n_out.value = 0
            self.control.write_data()
            print("TTL signals turned off on shutdown")
        except Exception as e:
            print(f"Error turning off TTL on shutdown: {e}")