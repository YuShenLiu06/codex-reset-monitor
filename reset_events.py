"""Third-party completed reset records, independent of forecast probability.

No account-quota access. Baseline existing history once; never replay an
attempted delivery or erase history. Only public-v1 confirmed_global records.
"""
import json
import re
from urllib.parse import urlsplit

import monitor as m

MAX_EVENT_AGE = 86400
MAX_RECORDS = 1000
STATUSES = {'baseline', 'queued', 'pending', 'sent', 'unknown', 'expired', 'abandoned'}
PRECISIONS = {'exact', 'approximate', 'announcement_fallback'}


def identity(source_key):
    return 'reset:' + m.digest(m.SOURCE_URL + '|confirmed_global|' + source_key)


def source_url(value):
    if not isinstance(value, str) or len(value) > 512:
        raise m.MonitorError('Invalid reset event source')
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise m.MonitorError('Malformed reset event source') from None
    if (parsed.scheme != 'https' or parsed.netloc != 'x.com' or parsed.query or parsed.fragment
            or not re.fullmatch(r'/(?:thsottiaux|OpenAI)/status/[0-9]{8,30}/?', parsed.path)):
        raise m.MonitorError('Unapproved reset event source')
    return value.rstrip('/')


def validate_record(record):
    if (not isinstance(record, dict) or not isinstance(record.get('status'), str)
            or record['status'] not in STATUSES):
        raise m.StateError('Invalid reset delivery record')
    key = record.get('source_key')
    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,199}', key):
        raise m.StateError('Invalid reset source key')
    try:
        source_url(record.get('source'))
        m.parse_time(record.get('reset_at'), 'reset.reset_at')
        m.parse_time(record.get('first_seen_at'), 'reset.first_seen_at')
        if not isinstance(record.get('precision'), str) or record['precision'] not in PRECISIONS:
            raise ValueError()
        if record['status'] in ('pending', 'sent', 'unknown', 'abandoned'):
            m.parse_time(record.get('attempted_at'), 'reset.attempted_at')
        elif 'attempted_at' in record:
            raise ValueError()
    except (m.MonitorError, ValueError):
        raise m.StateError('Invalid reset delivery metadata') from None


def validate_reset_state(value):
    if (not isinstance(value, dict) or value.get('version') != 1 or type(value.get('version')) is not int
            or not isinstance(value.get('records'), dict) or len(value['records']) > MAX_RECORDS):
        raise m.StateError('Invalid reset event ledger; preserve history')
    try:
        for field in ('initialized_at', 'last_checked_at', 'watermark'):
            m.parse_time(value.get(field), 'reset.' + field)
        if not isinstance(value.get('history_digest'), str) or not re.fullmatch(r'[a-f0-9]{40}', value['history_digest']):
            raise ValueError()
    except (m.MonitorError, ValueError):
        raise m.StateError('Invalid reset cursor; preserve history') from None
    for key, record in value['records'].items():
        validate_record(record)
        if key != identity(record['source_key']):
            raise m.StateError('Reset event identity mismatch')
    return value


def history(raw, snapshot, now):
    rows = raw['viewModel'].get('recentHistory')
    if rows is None:
        return None, 0
    if not isinstance(rows, list) or len(rows) > 50:
        raise m.MonitorError('Invalid reset history shape')
    events = {}; future = 0
    for item in rows:
        if not isinstance(item, dict):
            raise m.MonitorError('Invalid reset history record')
        # Notices, banked credit distribution, regular resets and references are
        # different semantics. Never infer completion from labels/probabilities.
        if item.get('recordKind') != 'confirmed_global':
            continue
        if item.get('sourceKind') != 'direct_post':
            continue
        key = item.get('key')
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,199}', key):
            raise m.MonitorError('Invalid reset event key')
        timestamp = m.parse_time(item.get('resetAt'), 'history.resetAt')
        if timestamp > min(now, snapshot.checked_at, snapshot.generated_at):
            future += 1
            continue
        precision = item.get('executionTimePrecision')
        if not isinstance(precision, str) or precision not in PRECISIONS:
            raise m.MonitorError('Unsupported reset time precision')
        event = {'source_key': key, 'source': source_url(item.get('source')),
                 'reset_at': m.iso(timestamp), 'precision': precision}
        if key in events and events[key] != event:
            raise m.MonitorError('Conflicting reset event identities')
        events[key] = event
    return sorted(events.values(), key=lambda e: (e['reset_at'], e['source_key'])), future


def observe(raw, snapshot, state, now):
    """Queue new records durably before dispatch; baseline never sends history."""
    events, future = history(raw, snapshot, now)
    if events is None:
        return False, [], {'status': 'history_unavailable'}
    fingerprint = m.digest(json.dumps(events, sort_keys=True, separators=(',', ':')))
    cursor = state.get('reset_events'); initial = cursor is None
    if initial:
        cursor = {'version': 1, 'initialized_at': m.iso(now), 'last_checked_at': m.iso(snapshot.checked_at),
                  'watermark': max((e['reset_at'] for e in events), default=m.iso(now)),
                  'history_digest': fingerprint, 'records': {}}
        state['reset_events'] = cursor
    else:
        last = m.parse_time(cursor['last_checked_at'], 'reset.last_checked_at')
        if snapshot.checked_at < last:
            return False, [], {'status': 'source_rollback'}
        if snapshot.checked_at == last and cursor['history_digest'] != fingerprint:
            return False, [], {'status': 'same_timestamp_history_revision'}
        if m.parse_time(cursor['watermark'], 'reset.watermark') > now:
            raise m.StateError('Future reset cursor; refusing to discard history')
    before = json.dumps(cursor, sort_keys=True)
    watermark = m.parse_time(cursor['watermark'], 'reset.watermark')
    aliases = {r['source']: key for key, r in cursor['records'].items()}
    revised = 0
    for event in events:
        key = identity(event['source_key']); timestamp = m.parse_time(event['reset_at'], 'reset.reset_at')
        existing = cursor['records'].get(key)
        if existing is not None:
            if any(existing[field] != event[field] for field in event):
                revised += 1  # Freeze original identity/time; edits are not another reset.
            continue
        if len(cursor['records']) >= MAX_RECORDS:
            raise m.StateError('Reset ledger limit reached; preserve and archive history manually')
        alias = event['source'] in aliases
        if initial or alias or timestamp <= watermark:
            status = 'baseline'
        elif (now - timestamp).total_seconds() > MAX_EVENT_AGE:
            status = 'expired'
        else:
            status = 'queued'
        cursor['records'][key] = dict(event, status=status, first_seen_at=m.iso(now))
        aliases[event['source']] = key
        if not initial and not alias and timestamp > watermark:
            watermark = timestamp
    cursor.update(watermark=m.iso(watermark), last_checked_at=m.iso(snapshot.checked_at), history_digest=fingerprint)
    # A queued record survives a crash/lost state-write acknowledgement, but is
    # sent only while the current healthy history still confirms the same data.
    present = {identity(e['source_key']): e for e in events}; ready = []
    for key, record in cursor['records'].items():
        if record['status'] != 'queued':
            continue
        timestamp = m.parse_time(record['reset_at'], 'reset.reset_at')
        if (now - timestamp).total_seconds() > MAX_EVENT_AGE:
            record['status'] = 'expired'
        elif key in present and all(record[field] == present[key][field] for field in present[key]):
            ready.append(key)
    changed = initial or before != json.dumps(cursor, sort_keys=True)
    return changed, sorted(ready, key=lambda k: (cursor['records'][k]['reset_at'], k)), {
        'status': 'baseline_initialized' if initial else 'observed', 'queued': len(ready),
        'future_records_ignored': future, 'history_revisions_ignored': revised}


def alert_for(key, record):
    when = m.parse_time(record['reset_at'], 'reset.reset_at')
    label = {'exact': '数据源标记时间', 'approximate': '数据源近似时间',
             'announcement_fallback': '数据源采用的完成公告时间'}[record['precision']]
    return m.Alert(key, 'reset_recorded',
        'Codex 重置事件记录\n数据源记录到公共重置事件。\n'
        f'{label}：{m.local_time(when)}（UTC+08:00）。\n'
        '这不是本人账户额度已恢复的确认；请在 Codex 查看自己的实际额度。\n'
        '不根据概率下降、预告窗口或备用重置卡配发推断已重置。\n'
        f'第三方记录：{m.SOURCE_URL}\n记录引用：{record["source"]}')


def dispatch(key, config, store, state, now, send, sender):
    records = state['reset_events']['records']; record = records[key]
    if record['status'] != 'queued':
        return {'status': 'duplicate_suppressed', 'event_key': key, 'kind': 'reset_recorded'}
    if (any(r['status'] in ('pending', 'unknown') for r in records.values())
            or any(r['status'] in ('pending', 'unknown') for r in state.get('acceptance_tests', {}).values())):
        return {'status': 'blocked_unresolved_delivery', 'event_key': key, 'kind': 'reset_recorded'}
    alert = alert_for(key, record)
    if not send:
        return {'status': 'would_alert', 'event_key': key, 'kind': alert.kind, 'message': alert.text}
    m.validate_sender_config(config)
    record.update(status='pending', attempted_at=m.iso(now)); store.write(state)
    try:
        receipt = sender(config, alert)
    except Exception as exc:
        record.update(status='unknown', failure_type=type(exc).__name__); store.write(state)
        raise m.SendUnknown('Reset record delivery unconfirmed; no automatic retry') from None
    record.update(status='sent', sent_at=m.iso(now), receipt=receipt); store.write(state)
    # Forecast cooldown/ledger is intentionally untouched by reset deliveries.
    return {'status': 'sent', 'event_key': key, 'kind': alert.kind, 'receipt': receipt}
