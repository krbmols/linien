"""Bring the locking panel up against a server, without a Red Pitaya.

    python tests/test_locking_panel.py

The panel is exercised the way the GUI drives it -- ready() then
connection_established() -- against two servers: one that has the wavemeter
lock and one that does not.  Loading the .ui and poking widgets is not enough
to catch anything here; every interesting failure lives in the code that runs
once a connection exists.
"""

import os
import sys
from os import path

ROOT = path.join(path.dirname(path.abspath(__file__)), '..')
sys.path.insert(0, ROOT)
# The .ui refers to linien's custom widget classes by bare module name, the
# same way linien.gui.widgets puts its ui folder on the path.
sys.path.insert(0, path.join(ROOT, 'linien', 'gui', 'ui'))

try:
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PyQt5 import QtWidgets
except ImportError:
    print('PyQt5 is not installed; skipping')
    sys.exit(0)

from linien.server.parameters import Parameters

failures = []


def check(name, condition, detail=''):
    if condition:
        print('  ok   %s' % name)
    else:
        print('  FAIL %s %s' % (name, detail))
        failures.append(name)


class FakeControl:
    def write_data(self):
        pass

    def pause_acquisition(self):
        pass

    def continue_acquisition(self):
        pass

    def start_lock(self):
        pass


class FakeApp:
    def __init__(self, parameters):
        self.parameters = parameters
        self.control = FakeControl()


def old_server_parameters():
    """What a Red Pitaya running the previous server exposes: no wavemeter."""
    new = Parameters()

    class OldParameters:
        pass

    old = OldParameters()
    for name in dir(new):
        if name.startswith('_'):
            continue
        if name.startswith('wavemeter') or name == 'watch_wavemeter_lock':
            continue
        setattr(old, name, getattr(new, name))
    return old


def build_panel(parameters):
    from linien.gui.ui.locking_panel import LockingPanel
    panel = LockingPanel()
    panel.app = lambda: FakeApp(parameters)
    panel.ready()
    panel.connection_established()
    return panel


app = QtWidgets.QApplication([])

print('against an up-to-date server')
parameters = Parameters()
try:
    panel = build_panel(parameters)
    check('connection_established runs', True)
except Exception as error:
    check('connection_established runs', False, '(%s: %s)'
          % (type(error).__name__, error))
    panel = None

if panel is not None:
    container = panel.ids.lock_control_container
    index = container.indexOf(panel.ids.wavemeter_mode)
    check('wavemeter detected', panel.has_wavemeter)
    check('tab enabled', container.isTabEnabled(index))
    check('setpoint shown', panel.ids.wavemeter_setpoint.value() > 0)
    check('four tabs', container.count() == 4)

    # every tab, as clicking through them does
    for idx in range(container.count()):
        try:
            panel.lock_mode_changed(idx)
        except Exception as error:
            check('selecting tab %d' % idx, False, '(%s)' % error)
    check('selecting every tab', True)
    check('wavemeter mode set by its tab',
          parameters.wavemeter_lock_automatic_mode.value is False
          or parameters.wavemeter_lock_automatic_mode.value is True)
    panel.lock_mode_changed(2)
    check('tab 2 selects the wavemeter mode',
          parameters.wavemeter_lock_automatic_mode.value
          and not parameters.automatic_mode.value)
    panel.lock_mode_changed(3)
    check('tab 3 is manual',
          not parameters.automatic_mode.value
          and not parameters.relock_automatic_mode.value
          and not parameters.wavemeter_lock_automatic_mode.value)
    panel.reset_lock_failed()
    check('reset_lock_failed runs', True)

print('against a server without the wavemeter lock')
old = old_server_parameters()
try:
    panel = build_panel(old)
    check('connection_established runs', True)
except Exception as error:
    check('connection_established runs', False, '(%s: %s)'
          % (type(error).__name__, error))
    panel = None

if panel is not None:
    container = panel.ids.lock_control_container
    index = container.indexOf(panel.ids.wavemeter_mode)
    check('wavemeter not detected', not panel.has_wavemeter)
    check('tab disabled', not container.isTabEnabled(index))
    check('other tabs still enabled',
          all(container.isTabEnabled(i) for i in range(container.count())
              if i != index))
    check('label says what to do',
          'install_relock_server' in panel.ids.wavemeter_reading_label.text(),
          '(%s)' % panel.ids.wavemeter_reading_label.text())

    # clicking through the tabs must not raise: this is what actually broke
    for idx in range(container.count()):
        try:
            panel.lock_mode_changed(idx)
        except Exception as error:
            check('selecting tab %d' % idx, False,
                  '(%s: %s)' % (type(error).__name__, error))
    check('selecting every tab', True)

    try:
        panel.reset_lock_failed()
        check('reset_lock_failed runs', True)
    except Exception as error:
        check('reset_lock_failed runs', False, '(%s)' % error)

    # the tab is disabled, but its handlers must not explode if reached
    for name in ('wavemeter_url_changed', 'wavemeter_use_raw_changed',
                 'watch_wavemeter_changed', 'wavemeter_auto_offset_changed',
                 'start_wavemeter_lock_selection',
                 'stop_wavemeter_lock_selection'):
        try:
            getattr(panel, name)()
        except Exception as error:
            check('%s is safe' % name, False,
                  '(%s: %s)' % (type(error).__name__, error))
    check('every wavemeter handler is safe', True)

print()
if failures:
    print('%d check(s) failed: %s' % (len(failures), ', '.join(failures)))
    sys.exit(1)
print('all checks passed')
