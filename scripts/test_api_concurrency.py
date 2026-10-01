#!/usr/bin/env python3
"""Bounded Sixtyfour submission sweep. No retries, polling, or redirects.

Run from the repository root:
  backend/.venv-agent/bin/python scripts/test_api_concurrency.py

Defaults: 50 POSTs at each of 1,2,4,8,16,32 workers (300 total).
Accepted submissions may be billable. Results measure submission acceptance,
not job completion or the maximum number of concurrent remote jobs. Repeated
inputs may be cached/deduplicated; cooldown does not guarantee a quota reset.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import time

import requests
from requests.adapters import HTTPAdapter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.blocks.sixtyfour import setting

URL = 'https://api.sixtyfour.ai/find-email-async'


def submit(payload, key, concurrency, number, input_index, timeout):
    """Exactly one application-level HTTP attempt; never resubmit failures."""
    record = dict(concurrency=concurrency, request=number, input_index=input_index,
                  started_at=datetime.now(timezone.utc).isoformat(),
                  status=None, outcome=None, task_id=None)
    started = time.monotonic()
    try:
        with requests.Session() as session:
            session.mount('https://', HTTPAdapter(max_retries=0))
            response = session.post(URL, headers={'x-api-key': key}, json=payload,
                                    timeout=timeout, allow_redirects=False)
            record['status'] = response.status_code
            record['rate_headers'] = {
                k: v for k, v in response.headers.items()
                if 'ratelimit' in k.lower().replace('-', '') or k.lower() == 'retry-after'
            }
            if 200 <= response.status_code < 300:
                try:
                    body = response.json()
                except ValueError:
                    body = None
                task_id = body.get('task_id') if isinstance(body, dict) else None
                if isinstance(task_id, str) and task_id and '/' not in task_id:
                    record.update(outcome='accepted', task_id=task_id)
                else:
                    record['outcome'] = 'invalid_response'
            else:
                record['outcome'] = 'rate_limited' if response.status_code == 429 else 'http_error'
    except requests.RequestException as exc:
        # Do not log exception text: it can contain credential/proxy information.
        record.update(outcome='transport_error', error_type=type(exc).__name__)
    record['latency_seconds'] = round(time.monotonic() - started, 4)
    return record


def summarize(records, concurrency, elapsed):
    outcomes = Counter(r['outcome'] for r in records)
    latencies = sorted(r['latency_seconds'] for r in records)
    return dict(concurrency=concurrency, attempts=len(records),
                accepted=outcomes['accepted'], rate_limited=outcomes['rate_limited'],
                other_errors=len(records) - outcomes['accepted'] - outcomes['rate_limited'],
                statuses=dict(Counter(str(r['status']) for r in records)),
                elapsed_seconds=round(elapsed, 3),
                requests_per_second=round(len(records) / elapsed, 3),
                p95_seconds=latencies[math.ceil(len(latencies) * .95) - 1])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=ROOT / 'inputs/input.csv')
    parser.add_argument('--levels', default='1,2,4,8,16,32')
    parser.add_argument('--calls-per-level', type=int, default=50)
    parser.add_argument('--cooldown', type=float, default=60)
    parser.add_argument('--timeout', type=float, default=45)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    try:
        levels = [int(x) for x in args.levels.split(',')]
    except ValueError:
        parser.error('--levels must contain comma-separated positive integers')
    if (not levels or min(levels) < 1 or args.calls_per_level < 1
            or not math.isfinite(args.cooldown) or args.cooldown < 0
            or not math.isfinite(args.timeout) or args.timeout <= 0):
        parser.error('Levels, calls and timeout must be positive; cooldown must be nonnegative')
    with args.input.open(newline='', encoding='utf-8-sig') as handle:
        rows = list(csv.DictReader(handle))
    leads = [{k: row[k].strip() for k in ('name', 'company', 'linkedin')
              if row.get(k, '').strip()} for row in rows]
    leads = [lead for lead in leads if lead.get('linkedin') or
             (lead.get('name') and lead.get('company'))]
    if not leads:
        parser.error('Input needs linkedin or name + company columns with nonempty values')
    planned = len(levels) * args.calls_per_level
    print('Endpoint: %s; budget: %d POSTs; %d input records; no retries or polling.' %
          (URL, planned, len(leads)), flush=True)
    if args.dry_run:
        return 0
    key = setting('SIXTYFOUR_API_KEY')
    if not key or key == 'replace_with_your_key':
        parser.error('Set SIXTYFOUR_API_KEY in environment or backend/.env')
    output = args.output or ROOT / 'outputs' / ('concurrency-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    output.mkdir(parents=True, exist_ok=False)
    report = dict(endpoint=URL, planned_calls=planned, input_records=len(leads),
                  calls_per_level=args.calls_per_level, levels=levels,
                  cooldown_seconds=args.cooldown, timeout_seconds=args.timeout,
                  retries=0, polling=False, stages=[], stop_reason=None)
    print('Results: %s' % output, flush=True)
    with (output / 'attempts.jsonl').open('w') as log:
        for index, concurrency in enumerate(levels):
            if index:
                print('Cooldown: %gs' % args.cooldown, flush=True)
                time.sleep(args.cooldown)
            started = time.monotonic()
            records = []
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = []
                for number in range(args.calls_per_level):
                    input_index = number % len(leads)
                    payload = dict(lead=leads[input_index], mode='PROFESSIONAL', verify_emails=False)
                    futures.append(pool.submit(submit, payload, key, concurrency,
                                               number + 1, input_index, args.timeout))
                for future in as_completed(futures):
                    record = future.result()
                    records.append(record)
                    log.write(json.dumps(record) + '\n')
                    log.flush()
            stage = summarize(records, concurrency, time.monotonic() - started)
            report['stages'].append(stage)
            # Finish this bounded stage, but avoid further stages with invalid
            # credentials/payloads or an entirely unavailable network.
            if any(r['status'] in (400, 401, 402, 403, 404, 422) for r in records):
                report['stop_reason'] = 'Non-rate-limit client error; fix before continuing'
            elif all(r['outcome'] in ('transport_error', 'invalid_response') for r in records):
                report['stop_reason'] = 'No valid HTTP outcomes in stage'
            report['actual_calls'] = sum(s['attempts'] for s in report['stages'])
            (output / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
            print(json.dumps(stage), flush=True)
            if report['stop_reason']:
                print('Stopped: ' + report['stop_reason'], flush=True)
                return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
