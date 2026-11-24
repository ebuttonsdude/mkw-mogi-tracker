"""Bounded, payload-free diagnostics shared by the launch and service layers.

Only catalogued events and finite numeric metrics reach our handlers. Never pass
request bodies, URLs, headers, names, paths or exception text to logging. Third
party/root loggers are deliberately left untouched.
"""

import errno
import logging
from logging.handlers import RotatingFileHandler
import math
from pathlib import Path
import sys
import time

MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 3
# Levels and operator-facing text are trusted source, not runtime payloads.
EVENTS = {
    'app': {
        'started': (logging.INFO, 'Tracker started'),
        'stopped': (logging.INFO, 'Tracker stopped'),
        'failed': (logging.ERROR, 'Tracker operation failed; check data-folder access and installed dependencies'),
        'cleanup_failed': (logging.ERROR, 'Tracker shutdown incomplete; restart before using the data folder'),
        'output_failed': (logging.ERROR, 'Cannot update standings for OBS; check free space and data-folder access'),
    },
    'http': {
        'listening': (logging.INFO, 'Dashboard listener ready'),
        'bind_failed': (logging.ERROR, 'Cannot listen; check the bind address and whether the port is already in use'),
        'response': (logging.DEBUG, 'HTTP response'),
        'not_found': (logging.WARNING, 'HTTP resource not found; a requested page, file or record is unavailable'),
        'rejected': (logging.WARNING, 'HTTP request rejected; check the dashboard action and local origin'),
        'failed': (logging.ERROR, 'HTTP operation failed; check data-folder access and installed dependencies'),
        'disconnected': (logging.DEBUG, 'HTTP client disconnected or timed out'),
    },
    'capture': {
        'starting': (logging.INFO, 'Starting capture and recognizer'),
        'ready': (logging.INFO, 'Capture connected; recognizer ready'),
        'sample': (logging.DEBUG, 'Capture sample processed'),
        'unavailable': (logging.ERROR, 'Capture unavailable; check the input connection and selected reader; retrying'),
        'failed': (logging.ERROR, 'Capture setup failed; check the input connection and selected reader'),
        'cleanup_failed': (logging.ERROR, 'Capture cleanup incomplete; restart the tracker'),
        'stopped': (logging.INFO, 'Capture stopped'),
    },
    'obs': {
        'connecting': (logging.DEBUG, 'Connecting to local OBS WebSocket'),
        'connected': (logging.INFO, 'OBS WebSocket connected'),
        'failed': (logging.ERROR, 'OBS connection failed; check the WebSocket server and password'),
        'closed': (logging.DEBUG, 'OBS WebSocket closed'),
    },
    'recognition': {
        'external_started': (logging.INFO, 'External vision reader selected'),
        'external_failed': (logging.ERROR, 'External vision request failed; check the configured server and vision model'),
        'external_stopped': (logging.INFO, 'External vision reader closed; remote computation may continue'),
        'starting': (logging.INFO, 'Starting name-reader worker'),
        'started': (logging.INFO, 'Name-reader worker started'),
        'result': (logging.DEBUG, 'Name-reader batch processed'),
        'failed': (logging.ERROR, 'Name reading failed; check the selected reader installation and available memory'),
        'cleanup_failed': (logging.ERROR, 'Name-reader cleanup incomplete; restart the tracker'),
        'stopped': (logging.INFO, 'Name-reader worker stopped'),
    },
    'imports': {
        'started': (logging.INFO, 'Video import started'),
        'reader_ready': (logging.DEBUG, 'Video import recognizer ready'),
        'progress': (logging.DEBUG, 'Video import progress'),
        'failed': (logging.ERROR, 'Video import failed; check the recording and selected reader'),
        'cleanup_failed': (logging.ERROR, 'Video import cleanup incomplete; restart the tracker'),
        'completed': (logging.INFO, 'Video import completed; results remain subject to review'),
        'cancelled': (logging.INFO, 'Video import cancelled; already found results retained'),
    },
}
METRICS = frozenset({'port', 'status', 'sample_count', 'acquisition_ms', 'recognition_ms',
                     'cycle_ms', 'row_count', 'percent', 'observed_races', 'observed_results'})


def _error_kind(error):
    # Do not stringify exceptions, including their class names or filenames.
    if isinstance(error, PermissionError):
        return 'permission-denied'
    if isinstance(error, OSError) and error.errno == errno.EADDRINUSE:
        return 'port-in-use'
    if isinstance(error, FileNotFoundError):
        return 'file-not-found'
    if isinstance(error, TimeoutError):
        return 'timeout'
    if isinstance(error, ConnectionError):
        return 'connection-failed'
    if isinstance(error, (ValueError, TypeError)):
        return 'invalid-input'
    if isinstance(error, OSError):
        return 'io-error'
    return 'operation-error'


def log_event(component, event, *, error=None, **metrics):
    """Emit a fixed event, optional safe failure category and numeric metrics.

    Extend EVENTS for new service events rather than logging raw endpoint/model
    responses. Unconfigured services are quiet, as in embedded/test applications.
    """
    level, message = EVENTS[component][event]
    logger = logging.getLogger(f'mkw_tracker.diagnostics.{component}')
    if not logger.handlers:
        return
    values = {key: value for key, value in metrics.items()
              if key in METRICS and type(value) in (int, float) and abs(value) <= 1e12 and math.isfinite(value)}
    logger.log(level, message, extra={'diagnostic_event': event, 'diagnostic_metrics': values,
                                    'diagnostic_error': _error_kind(error) if error is not None else None})


class _EventFilter(logging.Filter):
    def __init__(self, component):
        super().__init__()
        self.component = component

    def filter(self, record):
        return (record.name == f'mkw_tracker.diagnostics.{self.component}'
                and getattr(record, 'diagnostic_event', None) in EVENTS[self.component])


class _EventFormatter(logging.Formatter):
    converter = time.gmtime

    def format(self, record):
        component = record.name.rsplit('.', 1)[-1]
        level, message = EVENTS[component][record.diagnostic_event]
        # Do not use getMessage(), exc_info or stack_info: those can contain
        # credentials, local source/recording paths and image/request payloads.
        fields = [f'{key}={value}' for key, value in record.diagnostic_metrics.items()
                  if key in METRICS and type(value) in (int, float) and abs(value) <= 1e12 and math.isfinite(value)]
        kind = record.diagnostic_error
        if kind in {'permission-denied', 'port-in-use', 'file-not-found', 'timeout',
                    'connection-failed', 'invalid-input', 'io-error', 'operation-error'}:
            fields.append(f'reason={kind}')
        suffix = ' ' + ' '.join(fields) if fields else ''
        return (f'{self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ")} '
                f'{logging.getLevelName(level)} [{component}] {message}{suffix}')


class _RotatingHandler(RotatingFileHandler):
    _reported_error = False

    def handleError(self, record):
        # Standard logging's debug fallback prints raw records and tracebacks.
        if not self._reported_error:
            self._reported_error = True
            print('ERROR [app] Cannot write diagnostic log; check free space and log-folder access.',
                  file=sys.stdout, flush=True)


class LoggingSession:
    """Own only these service handlers; closing restores prior logger settings."""

    def __init__(self):
        self._bindings = []

    def close(self):
        for logger, handlers, previous in reversed(self._bindings):
            for handler in handlers:
                logger.removeHandler(handler)
                handler.close()
            logger.setLevel(previous[0])
            logger.propagate, logger.disabled = previous[1:]
        self._bindings.clear()


def configure_logging(data_dir, *, verbose=False, stdout=None):
    """Create logs/ with INFO files (DEBUG with verbose) and WARNING+ stdout.

    Call once at launch and close after all services stop. No root handlers,
    third-party log levels, payload tracing or global exception hooks change.
    """
    directory = Path(data_dir) / 'logs'
    directory.mkdir(parents=True, exist_ok=True)
    session = LoggingSession()
    try:
        for component in EVENTS:
            logger = logging.getLogger(f'mkw_tracker.diagnostics.{component}')
            console = logging.StreamHandler(sys.stdout if stdout is None else stdout)
            console.setLevel(logging.WARNING)
            file_handler = _RotatingHandler(directory / f'{component}.log', maxBytes=MAX_BYTES,
                                            backupCount=BACKUP_COUNT, encoding='utf-8')
            file_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
            handlers = (console, file_handler)
            previous = (logger.level, logger.propagate, logger.disabled)
            session._bindings.append((logger, handlers, previous))
            for handler in handlers:
                handler.setFormatter(_EventFormatter())
                handler.addFilter(_EventFilter(component))
                logger.addHandler(handler)
            logger.setLevel(logging.DEBUG if verbose else logging.INFO)
            logger.propagate, logger.disabled = False, False
    except BaseException:
        session.close()
        raise
    return session
