import numpy as np
from PyQt5 import QtGui, QtWidgets, QtWidgets
import time
import threading

from linien.common import ANALOG_OUT_V, convert_channel_mixing_value, FAST_OUT1, FAST_OUT2, \
    ANALOG_OUT0
from linien.gui.utils_gui import param2ui
from linien.gui.widgets import CustomWidget
from linien.client.connection import MHz, Vpp


class GeneralPanel(QtWidgets.QWidget, CustomWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.load_ui('general_panel.ui')
        self._updating_dropdowns = False  # Flag to prevent infinite update loops

    def ready(self):
        self.ids.channel_mixing_slider.valueChanged.connect(self.channel_mixing_changed)
        self.ids.dual_channel.stateChanged.connect(self.dual_channel_changed)

        self.ids.mod_channel.currentIndexChanged.connect(self.mod_channel_changed)
        self.ids.control_channel.currentIndexChanged.connect(self.control_channel_changed)
        self.ids.sweep_channel.currentIndexChanged.connect(self.sweep_channel_changed)
        self.ids.slow_control_channel.currentIndexChanged.connect(self.slow_control_channel_changed)
        self.ids.error_dc_offset_channel.currentIndexChanged.connect(self.error_dc_offset_channel_changed)

        self.ids.polarity_fast_out1.currentIndexChanged.connect(self.polarity_fast_out1_changed)
        self.ids.polarity_fast_out2.currentIndexChanged.connect(self.polarity_fast_out2_changed)
        self.ids.polarity_analog_out0.currentIndexChanged.connect(self.polarity_analog_out0_changed)

        for idx in range(4):
            if idx == 0:
                continue
            element = getattr(self.ids, 'analog_out_%d' % idx)
            element.setKeyboardTracking(False)
            element.valueChanged.connect(
                lambda value, idx=idx: self.change_analog_out(idx)
            )

        # Connect to application's aboutToQuit to set ANALOG OUT 2 to 0
        app = QtWidgets.QApplication.instance()
        app.aboutToQuit.connect(self.turn_off_analog_out_2_on_shutdown)

    def change_analog_out(self, idx):
        name = 'analog_out_%d' % idx
        getattr(self.parameters, name).value = int(getattr(self.ids, name).value() / ANALOG_OUT_V)
        self.control.write_data()

    def connection_established(self):
        params = self.app().parameters
        self.control = self.app().control
        self.parameters = params

        def dual_channel_changed(value):
            self.ids.dual_channel_mixing.setVisible(value)
            return value
        param2ui(
            params.dual_channel,
            self.ids.dual_channel,
            dual_channel_changed
        )

        param2ui(
            params.channel_mixing,
            self.ids.channel_mixing_slider,
            lambda value: value + 128
        )
        # this is required to update the descriptive labels in the beginning
        self.channel_mixing_changed()

        # Set initial states for mod_channel and error_dc_offset_channel
        self._updating_dropdowns = True
        self.ids.error_dc_offset_channel.setCurrentIndex(0)  # ANALOG OUT 2
        self.ids.mod_channel.setCurrentIndex(2)  # disabled
        self._updating_dropdowns = False
        # Ensure slider is connected and value is set on startup
        self.error_dc_offset_channel_changed(0)
        
        # Don't use param2ui for mod_channel as we want to override its default value
        # param2ui(params.mod_channel, self.ids.mod_channel)
        param2ui(params.control_channel, self.ids.control_channel)
        param2ui(params.sweep_channel, self.ids.sweep_channel)
        param2ui(params.pid_on_slow_enabled, self.ids.slow_control_channel)
        
        # We already set error_dc_offset_channel to index 0 (ANALOG OUT 0) above
        # self.ids.error_dc_offset_channel.setCurrentIndex(0)

        param2ui(params.polarity_fast_out1, self.ids.polarity_fast_out1)
        param2ui(params.polarity_fast_out2, self.ids.polarity_fast_out2)
        param2ui(params.polarity_analog_out0, self.ids.polarity_analog_out0)

        def show_polarity_settings(*args):
            used_channels = set((
                params.control_channel.value,
                params.sweep_channel.value,
            ))

            if params.pid_on_slow_enabled.value:
                used_channels.add(ANALOG_OUT0)

            self.ids.polarity_selector.setVisible(len(used_channels) > 1)

            def set_visibility(element, channel_id):
                element.setVisible(channel_id in used_channels)

            set_visibility(self.ids.polarity_container_fast_out1, FAST_OUT1)
            set_visibility(self.ids.polarity_container_fast_out2, FAST_OUT2)
            set_visibility(self.ids.polarity_container_analog_out0, ANALOG_OUT0)
        params.control_channel.on_change(show_polarity_settings)
        params.sweep_channel.on_change(show_polarity_settings)
        params.mod_channel.on_change(show_polarity_settings)
        params.pid_on_slow_enabled.on_change(show_polarity_settings)

        for idx in range(4):
            if idx == 0:
                continue
            name = 'analog_out_%d' % idx
            param2ui(
                getattr(params, name),
                getattr(self.ids, name),
                process_value=lambda v: ANALOG_OUT_V * v
            )

        # --- GPIO TTL control ---
        self.ids.ttl_high_button.clicked.connect(lambda: self.set_ttl_state(True))
        self.ids.ttl_low_button.clicked.connect(lambda: self.set_ttl_state(False))

        # Register listeners so the monitor tracks the actual parameter values,
        # including changes made server-side by the relock routine or by
        # lock_status_panel. on_change() also fires immediately with the current
        # value, which initialises the monitor.
        params.gpio_p_out.on_change(self.update_ttl_monitor)
        params.gpio_n_out.on_change(self.update_ttl_monitor)

    def channel_mixing_changed(self):
        value = int(self.ids.channel_mixing_slider.value()) - 128
        self.parameters.channel_mixing.value = value
        self.control.write_data()

        self.update_channel_mixing_slider(value)

    def dual_channel_changed(self):
        self.parameters.dual_channel.value = int(self.ids.dual_channel.checkState() > 0)
        self.control.write_data()

    def update_channel_mixing_slider(self, value):
        a_value, b_value = convert_channel_mixing_value(value)

        self.ids.chain_a_factor.setText('%d' % a_value)
        self.ids.chain_b_factor.setText('%d' % b_value)

    def mod_channel_changed(self, channel):
        if self._updating_dropdowns:
            return
            
        self.parameters.mod_channel.value = channel
        self.control.write_data()
        
        # Removed the code that sets Error DC Offset to disabled

    def control_channel_changed(self, channel):
        self.parameters.control_channel.value = channel
        self.control.write_data()

    def slow_control_channel_changed(self, channel):
        self.parameters.pid_on_slow_enabled.value = bool(channel)
        self.control.write_data()

    def sweep_channel_changed(self, channel):
        self.parameters.sweep_channel.value = channel
        self.control.write_data()

    def polarity_fast_out1_changed(self, polarity):
        self.parameters.polarity_fast_out1.value = bool(polarity)
        self.control.write_data()

    def polarity_fast_out2_changed(self, polarity):
        self.parameters.polarity_fast_out2.value = bool(polarity)
        self.control.write_data()

    def polarity_analog_out0_changed(self, polarity):
        self.parameters.polarity_analog_out0.value = bool(polarity)
        self.control.write_data()

    def error_dc_offset_channel_changed(self, channel):
        print(f"Error DC Offset channel changed to: {channel}")
        if self._updating_dropdowns:
            return
        main_window = self.window()
        slider = getattr(main_window.ids, 'error_dc_offset_slider', None)
        # Disconnect any previous connection
        try:
            slider.valueChanged.disconnect(self._update_analog_out_2_from_slider)
        except Exception:
            pass
        if channel == 0:  # ANALOG OUT 2
            # Map slider value (0..100) to voltage (0..1.8V)
            def _update_analog_out_2_from_slider(value):
                voltage = (value / 100) * 1.8
                print(f"Voltage: {voltage}")
                self.parameters.analog_out_2.value = -voltage / ANALOG_OUT_V
                print(f"Analog out 2 value: {self.parameters.analog_out_2.value}")
                self.ids.analog_out_2.setValue(-voltage)
                self.control.write_data()
            self._update_analog_out_2_from_slider = _update_analog_out_2_from_slider
            slider.valueChanged.connect(self._update_analog_out_2_from_slider)
            slider.setEnabled(True)
            # Set initial value from slider
            self._update_analog_out_2_from_slider(slider.value())
            self.ids.analog_out_2.setEnabled(False)
        else:  # disabled
            self.parameters.analog_out_2.value = 0
            self.ids.analog_out_2.setValue(0.0)
            self.ids.analog_out_2.setEnabled(False)
            if slider:
                slider.setEnabled(False)
            self.control.write_data()

    def set_ttl_state(self, high):
        """Latch every DIO pin (both banks) HIGH (3.3 V) or LOW (0 V).

        This is a persistent state, not a pulse -- it stays until changed here
        or overridden by the relock routine.
        """
        value = 0b11111111 if high else 0b00000000
        self.parameters.gpio_p_out.value = value  # DIO0_P .. DIO7_P
        self.parameters.gpio_n_out.value = value  # DIO0_N .. DIO7_N
        self.control.write_data()

    def update_ttl_monitor(self, _value=None):
        """Refresh the TTL state monitor from the gpio_*_out parameters."""
        # May fire before connection_established() has finished.
        if not hasattr(self, 'parameters'):
            return

        p = self.parameters.gpio_p_out.value
        n = self.parameters.gpio_n_out.value

        if p == 0b11111111 and n == 0b11111111:
            text = 'HIGH  (3.3 V)'
            style = 'color: #ffffff; background: #c62828;'
        elif p == 0b00000000 and n == 0b00000000:
            text = 'LOW  (0 V)'
            style = 'color: #ffffff; background: #2e7d32;'
        else:
            text = 'MIXED'
            style = 'color: #000000; background: #ffb300;'

        self.ids.ttl_status_label.setStyleSheet(
            style + ' border: 1px solid #444444; border-radius: 3px; padding: 4px;'
        )
        self.ids.ttl_status_label.setText(text)
        self.ids.ttl_status_detail.setText(
            'gpio_p_out = {:08b}   gpio_n_out = {:08b}'.format(p, n)
        )

        # Grey out whichever button matches the current state.
        self.ids.ttl_high_button.setEnabled(text != 'HIGH  (3.3 V)')
        self.ids.ttl_low_button.setEnabled(text != 'LOW  (0 V)')

    def turn_off_analog_out_2_on_shutdown(self):
        try:
            self.parameters.analog_out_2.value = 0
            self.control.write_data()
            print("ANALOG OUT 2 set to 0 on shutdown")
        except Exception as e:
            print(f"Error setting ANALOG OUT 2 to 0 on shutdown: {e}")

        