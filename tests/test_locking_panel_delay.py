"""Bring the locking panel up and check the autolock relock delay field.

    python tests/test_locking_panel_delay.py

Loading the .ui is not enough to catch anything here: the interesting failures
live in the code that runs once a connection exists, so the panel is driven the
way the GUI drives it -- ready() then connection_established() -- against a
server that has the delay parameter and one that does not.
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
    """What a Red Pitaya that has not been redeployed exposes.

    The client only mirrors the parameters the server has, so an out-of-date
    server simply has no `watch_lock_delay`.
    """
    current = Parameters()

    class OldParameters:
        pass

    old = OldParameters()
    for name in dir(current):
        if name.startswith('_') or name == 'watch_lock_delay':
            continue
        setattr(old, name, getattr(current, name))
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
    check('relock delay detected', panel.has_watch_lock_delay)
    check('relock delay field enabled', panel.ids.watch_lock_delay.isEnabled())
    check('relock delay defaults to 0 (historic behaviour)',
          panel.ids.watch_lock_delay.value() == 0
          and parameters.watch_lock_delay.value == 0)
    panel.ids.watch_lock_delay.setValue(12.5)
    check('relock delay is written through in seconds',
          parameters.watch_lock_delay.value == 12.5,
          '(%s)' % parameters.watch_lock_delay.value)
    parameters.watch_lock_delay.value = 3
    check('relock delay is read back from the server',
          panel.ids.watch_lock_delay.value() == 3,
          '(%s)' % panel.ids.watch_lock_delay.value())

    # The autolock column must end in a vertical spacer. Its labels word-wrap
    # and so have an expanding vertical policy; without a spacer to soak up
    # the slack the layout hands it to them instead, which spreads the column
    # out and pushes the delay row below the fold.
    layout = panel.findChild(QtWidgets.QVBoxLayout, 'verticalLayout_5')
    last = layout.itemAt(layout.count() - 1) if layout is not None else None
    check('the autolock column ends in a spacer, so it does not spread out',
          last is not None and last.spacerItem() is not None,
          '(last item is %s)' % (last and last.widget() or last))

print('against a server without the relock delay')
old = old_server_parameters()
try:
    panel = build_panel(old)
    check('connection_established runs', True)
except Exception as error:
    check('connection_established runs', False, '(%s: %s)'
          % (type(error).__name__, error))
    panel = None

if panel is not None:
    check('relock delay not detected', not panel.has_watch_lock_delay)
    check('relock delay field disabled',
          not panel.ids.watch_lock_delay.isEnabled())
    check('relock delay field says what to do',
          'install_relock_server' in panel.ids.watch_lock_delay.toolTip(),
          '(%s)' % panel.ids.watch_lock_delay.toolTip())
    try:
        panel.ids.watch_lock_delay.setValue(5)
        panel.watch_lock_delay_changed()
        check('relock delay handler is safe', True)
    except Exception as error:
        check('relock delay handler is safe', False,
              '(%s: %s)' % (type(error).__name__, error))

print()
if failures:
    print('%d check(s) failed: %s' % (len(failures), ', '.join(failures)))
    sys.exit(1)
print('all checks passed')
