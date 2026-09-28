"""Sixtyfour transport shared by blocks. Credentials never enter browser state."""
import json
import math
import os
from pathlib import Path
import time
import requests

# Resolve project paths independently of the directory used to launch the server.
ROOT = Path(__file__).resolve().parents[2]


def setting(name, default=None):
    """Read a server setting: process environment first, then backend/.env."""
    if name in os.environ:
        return os.environ[name]
    env = ROOT / 'backend' / '.env'
    if env.exists():
        for line in env.read_text().splitlines():
            key, sep, value = line.strip().partition('=')
            if sep and key.strip() == name:
                return value.strip().strip('\"\'')
    return default


class APIError(RuntimeError):
    """Provider failure with optional HTTP status for row-vs-run error handling."""
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class SixtyfourClient:
    """Authenticated transport; tool modules choose endpoints and construct payloads."""
    BASE_URL = 'https://api.sixtyfour.ai'

    def __init__(self, context):
        self.context = context
        self.key = setting('SIXTYFOUR_API_KEY')
        if not self.key or self.key == 'replace_with_your_key':
            raise APIError('Set SIXTYFOUR_API_KEY in backend/.env or the server environment before running API blocks.', 401)
        self.job_timeout = max(1, int(setting('SIXTYFOUR_JOB_TIMEOUT', '900')))

    def request(self, method, path, payload=None, params=None, timeout=45):
        """Make a JSON request with bounded retries and cancellation checks.

        Rate limits can retry; server errors retry only for GET requests. A POST
        may already have created a billable job, so ambiguous failures are surfaced.
        """
        for attempt in range(3):
            self.context.check_cancelled()
            try:
                response = requests.request(method, self.BASE_URL + path,
                    headers={'x-api-key': self.key}, json=payload, params=params,
                    timeout=timeout)
            except requests.RequestException as exc:
                # Never resubmit a possibly accepted billable POST automatically.
                raise APIError('Sixtyfour connection failed (%s). The request was not resubmitted.' % type(exc).__name__)
            if (response.status_code == 429 or (method == 'GET' and response.status_code >= 500)) and attempt < 2:
                self.context.wait(2 ** (attempt + 1))
                continue
            if not response.ok:
                try:
                    detail = response.json().get('detail', response.reason)
                except (ValueError, AttributeError):
                    detail = response.reason
                message = str(detail).replace(self.key, '[redacted]')[:800]
                raise APIError('Sixtyfour HTTP %s: %s' % (response.status_code, message), response.status_code)
            try:
                result = response.json()
            except ValueError:
                raise APIError('Sixtyfour returned invalid JSON')
            if not isinstance(result, dict):
                raise APIError('Unexpected Sixtyfour response: expected an object')
            return result

    def job(self, path, payload, search=False):
        """Submit once, then poll a task until completion, terminal failure, or timeout.

        Search status returns the search identifier at the top level. Other jobs
        wrap their completed payload in result, which is unwrapped for the tool.
        """
        started = self.request('POST', path, payload)
        task_id = started.get('task_id')
        if not isinstance(task_id, str) or not task_id or '/' in task_id:
            raise APIError('Sixtyfour did not return a valid task_id')
        self.context.progress('Submitted remote job ' + task_id)
        # Monotonic time is unaffected by wall-clock adjustments while waiting.
        # Endpoint overrides preserve the existing default for manual workflows.
        # These bound one remote job, never the whole agent or workflow run.
        timeout_setting = ('SIXTYFOUR_SEARCH_JOB_TIMEOUT' if search else
                           'SIXTYFOUR_RESEARCH_JOB_TIMEOUT' if path in
                           ('/people-intelligence-async', '/company-intelligence-async', '/qa-agent-async') else None)
        job_timeout = self.job_timeout
        if timeout_setting:
            job_timeout = min(86400, max(1, int(setting(timeout_setting, str(job_timeout)))))
        deadline = time.monotonic() + job_timeout
        poll_path = ('/search/deep-search-status/' if search else '/job-status/') + task_id
        while time.monotonic() < deadline:
            result = self.request('GET', poll_path, timeout=min(30, max(1, deadline - time.monotonic())))
            status = str(result.get('status', '')).lower()
            if status == 'completed':
                if search:
                    return result
                if 'result' not in result:
                    raise APIError('Completed job is missing its result')
                return result['result']
            if status in ('failed', 'cancelled', 'terminated', 'timed_out', 'continued_as_new'):
                raise APIError('Remote job %s: %s' % (status, result.get('error') or task_id))
            if status not in ('queued', 'running', 'pending'):
                raise APIError('Unrecognized remote job status: ' + status)
            self.context.progress(result.get('progress_message') or ('Waiting for remote job ' + task_id))
            self.context.wait(min(5, max(0, deadline - time.monotonic())))
        raise APIError('Timed out waiting for remote job %s. It may still be running; it was not resubmitted.' % task_id)


def clean_value(value):
    """JSON-safe values for API requests, previews, metadata and CSV cells."""
    import pandas as pd
    if isinstance(value, dict):
        return {str(k): clean_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_value(v) for v in value]
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    # NumPy scalar types need conversion to ordinary Python numbers/booleans.
    if hasattr(value, 'item'):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def cell(value):
    """Store nested structures as JSON text so table cells can be exported to CSV."""
    value = clean_value(value)
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value


def missing(value):
    """Use the same null/blank definition for mapping, skipping, and merging values."""
    value = clean_value(value)
    return value is None or (isinstance(value, str) and not value.strip())
