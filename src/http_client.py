from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse
from collections import deque
import hashlib
import json
import logging
import random
import threading
import time
import requests

def utcnow():
    return datetime.now(timezone.utc).isoformat()

def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, default=str), encoding='utf-8')
    temp.replace(path)

class BackoffError(RuntimeError):
    def __init__(self, retry_at, status=None):
        self.retry_at, self.status = retry_at, status
        super().__init__(f'Upstream unavailable status={status}; retry after {retry_at:.1f}')

@dataclass
class FetchResult:
    payload: dict
    payload_hash: str
    body: bytes
    changed: bool
    status: int
    checked_at: str
    bytes_received: int

class CachedClient:
    def __init__(self, settings):
        self.settings = settings
        self.cache = settings.data_dir / 'cache'
        self.cache.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers['User-Agent'] = settings.user_agent
        self.lock = threading.RLock()
        self.next_request = 0.
        self.origin_block = {}
        self.failures = {}
        self.retry_at = {}
        self.recent = deque()
        self.requests_total = 0
        self.not_modified = 0
        self.same_hash = 0
        self.start = time.monotonic()
        self.log_path = settings.data_dir / 'requests.ndjson'

    @property
    def rps(self):
        now = time.monotonic()
        return sum(t > now - 1 for t in self.recent)

    def _record(self, **event):
        event.update(ts=utcnow(), req_s=self.rps, requests_total=self.requests_total)
        with self.log_path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(event) + '\n')
        logging.info('HTTP %s', json.dumps(event))

    def _delay(self, url, status, retry_after=None):
        n = min(self.failures.get(url, 0) + 1, 10)
        self.failures[url] = n
        delay = min(300., 5. * 2 ** (n - 1) + random.uniform(0, 5))
        if retry_after:
            try:
                specified = float(retry_after)
            except ValueError:
                try:
                    specified = (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds()
                except (ValueError, TypeError):
                    specified = 0
            delay = max(delay, specified)
        if status == 404:
            delay = max(delay, 300.)
        if status == 403:
            delay = max(delay, 600.)  # TSE documents a ten-minute IP block.
        until = time.monotonic() + delay
        self.retry_at[url] = until
        if status in (403, 429) or status is None or status >= 500:
            self.origin_block[urlparse(url).netloc] = until
        return until

    def fetch(self, url):
        with self.lock:
            now = time.monotonic()
            until = max(self.retry_at.get(url, 0), self.origin_block.get(urlparse(url).netloc, 0))
            if now < until:
                raise BackoffError(until)
            if self.next_request > now:
                time.sleep(self.next_request - now)
            self.next_request = time.monotonic() + 1 / self.settings.rps
            self.recent.append(time.monotonic())
            while self.recent and self.recent[0] < time.monotonic() - 60:
                self.recent.popleft()
            self.requests_total += 1
            key = hashlib.sha256(url.encode()).hexdigest()
            index_path = self.cache / f'{key}.index.json'
            previous = json.loads(index_path.read_text()) if index_path.exists() else {}
            headers = {}
            if previous.get('etag'):
                headers['If-None-Match'] = previous['etag']
            if previous.get('last_modified'):
                headers['If-Modified-Since'] = previous['last_modified']
            try:
                response = self.session.get(url, headers=headers, timeout=(5, 15))
            except requests.RequestException as exc:
                until = self._delay(url, None)
                self._record(url=url, status='network_error', bytes=0, error=type(exc).__name__)
                raise BackoffError(until) from exc
            status = response.status_code
            if status not in (200, 304):
                until = self._delay(url, status, response.headers.get('Retry-After'))
                self._record(url=url, status=status, bytes=len(response.content), backoff_seconds=round(until-time.monotonic(), 2))
                raise BackoffError(until, status)
            if status == 304:
                if not previous.get('hash'):
                    raise ValueError('304 received without cached body')
                body = (self.cache / (previous['hash'] + '.json')).read_bytes()
                self.not_modified += 1
            else:
                body = response.content
            digest = hashlib.sha256(body).hexdigest()
            # Cache even an unexpected response before validation for diagnostics.
            body_path = self.cache / (digest + '.json')
            if not body_path.exists():
                body_path.write_bytes(body)
            try:
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise ValueError('Expected a JSON object')
            except (ValueError, UnicodeDecodeError) as exc:
                until = self._delay(url, 502)
                self._record(url=url, status='invalid_json', bytes=len(body), hash=digest)
                raise BackoffError(until, 502) from exc
            changed = previous.get('hash') != digest
            self.same_hash += int(not changed and status == 200)
            checked = utcnow()
            atomic_json(index_path, dict(url=url, hash=digest, checked_at=checked,
                                        etag=response.headers.get('ETag', previous.get('etag')),
                                        last_modified=response.headers.get('Last-Modified', previous.get('last_modified'))))
            self.failures.pop(url, None)
            self.retry_at.pop(url, None)
            self._record(url=url, status=status, bytes=len(response.content), hash=digest, changed=changed)
            return FetchResult(payload, digest, body, changed, status, checked, len(response.content))
