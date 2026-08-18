"""Reading laser frequencies from the wavemeter web server.

The wavemeter runs a Dash app on the lab network that exposes the most recent
reading of each laser at ``/api/latest``.  This module is the RedPitaya side of
that link: a background thread polls the endpoint so that the acquisition
callback -- which drives the lock -- never blocks on a socket, and a lock loop
reads whatever the thread last managed to fetch.

Only the standard library is used, so nothing has to be installed on the
RedPitaya beyond what linien already needs.
"""

import json
import threading
from time import time
from urllib.request import urlopen

# The wavemeter applies a calibration correction derived from one reference
# laser.  If the laser being locked *is* that reference, the correction
# subtracts its drift back off and the lock would never see it move, so ask for
# uncalibrated frequencies by default.
DEFAULT_USE_RAW = True

DEFAULT_TIMEOUT = 2.0

# Saturation below which the wavemeter discards a reading as noise rather than
# light.  The server's own default is 0.05, inherited from its CSV logger, which
# is uncomfortably close to what a dim laser actually reads -- a dip below it
# makes the laser vanish from the answer entirely.  Ask for a lower floor and
# leave the margin to the user, who can watch the reported amplitude.
DEFAULT_MIN_AMP = 0.02

ENDPOINT = '/api/latest'


class WavemeterError(Exception):
    """The wavemeter could not be reached, or answered with nonsense."""


def build_url(base_url, setpoint, search_range, use_raw=DEFAULT_USE_RAW,
              min_amp=DEFAULT_MIN_AMP):
    """Query URL for one laser.

    ``base_url`` may be the server root ('http://192.168.0.119:8050') or the
    endpoint itself; both are accepted so a pasted address just works.
    """
    base = base_url.strip().rstrip('/')
    if not base:
        raise WavemeterError('no wavemeter address configured')
    if not base.endswith(ENDPOINT):
        base += ENDPOINT

    url = '%s?freq=%.6f&tol=%.6f&min_amp=%.4f' % (
        base, setpoint, search_range, min_amp)
    if use_raw:
        url += '&raw=1'
    return url


def read_once(base_url, setpoint, search_range, use_raw=DEFAULT_USE_RAW,
              min_amp=DEFAULT_MIN_AMP, timeout=DEFAULT_TIMEOUT,
              opener=urlopen):
    """Fetch one reading.

    Returns the laser report, or None if the wavemeter answered but has no
    reading for this laser.  Raises WavemeterError if the wavemeter could not
    be reached or its answer could not be understood -- the caller must tell
    those apart, because "no answer" says nothing about the laser whereas "no
    such frequency" says the laser is not where it should be.
    """
    url = build_url(base_url, setpoint, search_range, use_raw, min_amp)

    try:
        response = opener(url, timeout=timeout)
        try:
            payload = json.loads(response.read().decode('utf-8'))
        finally:
            close = getattr(response, 'close', None)
            if close is not None:
                close()
    except OSError as error:
        # URLError, HTTPError, socket timeouts and refused connections are all
        # OSError; catching the base class keeps every network failure inside
        # WavemeterError instead of leaking out into the acquisition callback.
        raise WavemeterError('cannot reach %s: %s' % (url, error))
    except ValueError as error:
        raise WavemeterError('bad response from %s: %s' % (url, error))

    if not isinstance(payload, dict):
        raise WavemeterError('unexpected response from %s' % url)

    if not payload.get('found'):
        return None

    laser = payload.get('laser')
    if not isinstance(laser, dict) or 'detuning_MHz' not in laser:
        raise WavemeterError('response from %s has no reading in it' % url)

    return laser


class WavemeterMonitor:
    """Polls one laser in the background and hands out the latest answer.

    The lock reads :meth:`latest` from the acquisition callback, so the poll
    must not happen there: an unreachable wavemeter would stall acquisition for
    the whole socket timeout on every attempt.  The thread never touches
    linien parameters -- it only fills a slot that the callback drains -- so
    parameter updates stay on the thread that owns them.
    """

    def __init__(self, base_url, setpoint, search_range,
                 use_raw=DEFAULT_USE_RAW, min_amp=DEFAULT_MIN_AMP,
                 poll_interval=1.0, timeout=DEFAULT_TIMEOUT,
                 reader=read_once):
        self.base_url = base_url
        self.setpoint = setpoint
        self.search_range = search_range
        self.use_raw = use_raw
        self.min_amp = min_amp
        self.poll_interval = poll_interval
        self.timeout = timeout
        self._reader = reader

        self._lock = threading.Lock()
        self._reading = None        # last laser report, or None
        self._error = None          # last WavemeterError message, or None
        self._time = None           # when that answer arrived
        self._reachable = False     # did the last attempt get an answer at all

        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run)
        self._thread.daemon = True
        self._thread.start()

    def stop(self):
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=self.timeout + self.poll_interval + 1)

    def poll(self):
        """One fetch, storing whatever came back.  Used by the thread and tests."""
        try:
            reading = self._reader(
                self.base_url, self.setpoint, self.search_range,
                use_raw=self.use_raw, min_amp=self.min_amp,
                timeout=self.timeout
            )
        except WavemeterError as error:
            with self._lock:
                self._reading = None
                self._error = str(error)
                self._reachable = False
                self._time = time()
            return

        with self._lock:
            self._reading = reading
            self._error = None
            self._reachable = True
            self._time = time()

    def _run(self):
        while not self._stop.is_set():
            self.poll()
            # Event.wait doubles as the sleep, so stop() is not held up by a
            # poll interval.
            self._stop.wait(self.poll_interval)

    def latest(self):
        """(reading, error, reachable, arrival).

        ``reading`` is None when the wavemeter has no measurement for this
        laser; ``error`` is set instead when it could not be reached at all.

        ``arrival`` is the local time this answer was stored, or None before
        the first poll.  Callers run far faster than the poll interval, so most
        calls return an answer they have already seen; comparing ``arrival``
        against the last one consumed is how a caller tells "no news" from
        "news that says nothing", which are not the same thing at all when
        deciding whether a laser has gone missing.
        """
        with self._lock:
            return self._reading, self._error, self._reachable, self._time
