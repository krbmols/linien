import os
from PyQt5.QtWidgets import QSlider, QCheckBox, QSpinBox, QDoubleSpinBox, \
    QTabWidget, QRadioButton, QComboBox, QLineEdit
from pyqtgraph.Qt import QtGui


def param2ui(parameter, element, process_value=lambda x: x):
    """Updates ui elements according to parameter values.

    Listens to parameter changes and sets the value of `element` automatically.
    Optionally, the value can be processed using `process_value`.
    This function should be used because it automatically blocks signal
    emission from the target element; otherwise this can cause nasty
    endless loops when quickly changing a paramater multiple times.
    """
    def on_change(value, element=element):
        element.blockSignals(True)

        value = process_value(value)

        if isinstance(element, (QSlider, QSpinBox, QDoubleSpinBox)):
            element.setValue(value)
        elif isinstance(element, (QCheckBox, QRadioButton)):
            element.setChecked(value)
        elif isinstance(element, (QTabWidget, QComboBox)):
            element.setCurrentIndex(int(value))
        elif isinstance(element, QLineEdit):
            element.setText(str(value))
        else:
            raise Exception('unsupported element type %s' % type(element))

        element.blockSignals(False)

    parameter.on_change(on_change)


def set_window_icon(window):
    icon_name = os.path.join(*os.path.split(__file__)[:-1], 'icon.ico')
    window.setWindowIcon(QtGui.QIcon(icon_name))


def color_to_hex(color):
    result = ''
    for part_idx in range(3):
        result += ('00' + hex(color[part_idx]).lstrip('0x'))[-2:]

    return '#' + result


def server_has_wavemeter_lock(parameters):
    """Whether the connected server knows about the wavemeter lock.

    The client mimics whatever parameters the server exposes, so an out-of-date
    Red Pitaya simply has none of them.  Both sides read the same VERSION file,
    so linien's version check passes and the mismatch would otherwise surface
    as an AttributeError from somewhere unrelated.
    """
    return hasattr(parameters, 'wavemeter_url')
