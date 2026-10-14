"""Explicit user-authorized installation test; never a fabricated reset forecast."""
import dataclasses
import json
import os
import re
from monitor import (Alert, Config, MonitorError, SendUnknown, StateError, iso,
                     utc_now, notification_sender, validate_sender_config, unresolved_delivery)
from github_state import GitHubStateStore


def send_test(request_id,store,*,sender=notification_sender,now=None):
    if not isinstance(request_id,str) or not re.fullmatch(r'[0-9a-f]{32}',request_id):raise MonitorError('Invalid test request ID')
    config=dataclasses.replace(Config(),sending_enabled=True)
    validate_sender_config(config)
    now=now or utc_now()
    with store.locked():
        state=store.read();records=state.setdefault('acceptance_tests',{})
        if request_id in records:
            record=records[request_id]
            if record['status']=='sent':return {'status':'duplicate_suppressed','request_id':request_id}
            raise SendUnknown('Previous installation test unresolved; never automatically resend')
        if unresolved_delivery(state):raise SendUnknown('Unresolved delivery blocks installation test')
        if len(records)>=100:raise StateError('Test ledger limit reached; preserve history')
        entry={'status':'pending','attempted_at':iso(now)}
        records[request_id]=entry
        store.write(state)  # Confirm remote reservation BEFORE any Feishu request.
        alert=Alert('installation-test:'+request_id,'installation_test',
            'Codex 安装测试通知\n这是用户明确授权的连接测试，不是提前重置预警，也不代表额度恢复。\n'
            '请求标识：'+request_id+'\n请核对接收者与此标识，再在安装器确认收到。')
        try:receipt=sender(config,alert)
        except Exception:
            entry['status']='unknown';store.write(state)
            raise SendUnknown('Installation test delivery unknown; no automatic retry') from None
        entry.update(status='sent',receipt=receipt,sent_at=iso(now))
        store.write(state)  # Failure leaves persisted pending and blocks a second send.
        return {'status':'sent','request_id':request_id}


def main():
    try:
        if os.environ.get('MONITOR_ENABLED')=='true':raise MonitorError('Disable monitoring before acceptance test')
        if not os.environ.get('FEISHU_NOTIFY_BACKEND'):os.environ.pop('FEISHU_NOTIFY_BACKEND',None)
        print(json.dumps(send_test(os.environ.get('TEST_REQUEST_ID',''),GitHubStateStore())))
        return 0
    except (MonitorError,OSError) as exc:
        print(json.dumps({'status':'error','error':str(exc)}));return 3


if __name__=='__main__':raise SystemExit(main())
