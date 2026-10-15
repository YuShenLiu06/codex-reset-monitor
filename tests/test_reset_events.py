"""Actual public DTO pairing plus synthetic event/CAS/failure variations. No sends."""
import contextlib,copy,dataclasses,json
from datetime import timedelta
from pathlib import Path
import unittest
from unittest.mock import patch
import monitor as m
import reset_events as r

FIXTURE=Path(__file__).parent/'fixtures/public-current-20261015.json'
BASE=m.parse_time('2026-10-15T06:21:00Z','now')

class MemoryStore:
    def __init__(self,state=None):self.state=copy.deepcopy(state or m.empty_state());self.writes=[];self.fail_at=None;self.lose_at=None
    @contextlib.contextmanager
    def locked(self):yield
    def read(self):return copy.deepcopy(m.validate_state(self.state))
    def write(self,state):
        number=len(self.writes)+1
        if number==self.fail_at:raise m.StateError('synthetic CAS/write failure')
        self.state=copy.deepcopy(m.validate_state(state));self.writes.append(copy.deepcopy(state))
        if number==self.lose_at:raise m.StateError('synthetic lost acknowledgement')

class ResetEventsTest(unittest.TestCase):
    def setUp(self):
        self.store=MemoryStore();self.calls=[];self.config=m.Config(sending_enabled=True)
        self.env=patch.dict(m.os.environ,{'FEISHU_NOTIFY_BACKEND':'webhook','FEISHU_WEBHOOK_URL':'https://open.feishu.cn/open-apis/bot/v2/hook/'+'x'*24,'FEISHU_SIGNING_SECRET':'synthetic'})
        self.env.start();self.pause=patch('monitor.time.sleep');self.pause.start()
    def tearDown(self):self.pause.stop();self.env.stop()
    def data(self,now=BASE,p=.2):
        raw=json.loads(FIXTURE.read_text());raw['checkedAt']=raw['dataHealth']['generatedAt']=m.iso(now);raw['viewModel']['probability24h']=p;return raw
    def event(self,now=BASE+timedelta(hours=1),number=1):
        return {'key':'test-confirmed-'+str(number),'recordKind':'confirmed_global','resetAt':m.iso(now-timedelta(minutes=8)),
                'executionTimePrecision':'announcement_fallback','sourceKind':'direct_post','source':'https://x.com/thsottiaux/status/'+str(2110595484180214100+number)}
    def changed(self,now=BASE+timedelta(hours=1),p=.2,number=1):
        raw=self.data(now,p);event=self.event(now,number);raw['viewModel']['recentHistory'].insert(0,event);raw['lastRandomResetAt']=event['resetAt'];return raw
    def sender(self,config,alert):
        if alert.kind=='reset_recorded':self.assertEqual(self.store.state['reset_events']['records'][alert.key]['status'],'pending')
        self.calls.append(alert);return 'synthetic_receipt'
    def run_data(self,raw=None,now=BASE,send=True,store=None,sender=None):
        return m.run_once(self.config,send=send,now=now,fetcher=lambda:raw or self.data(now),sender=sender or self.sender,store=store or self.store)
    def baseline(self):return self.run_data()
    def test_actual_public_capture_has_no_oct15_completion_and_baselines_without_sending(self):
        raw=json.loads(FIXTURE.read_text());snap=m.validate_snapshot(raw,BASE)
        events,_=r.history(raw,snap,BASE)
        self.assertEqual(max(e['reset_at'] for e in events),'2026-10-13T05:06:06Z')
        result=self.run_data(raw);self.assertEqual(result['reset_events']['status'],'baseline_initialized')
        self.assertEqual([a.kind for a in self.calls],['forecast24h'])  # capture p=.9; no reset notification
    def test_actual_before_after_capture_emits_one_completed_record_despite_forecast_cooldown(self):
        before=json.loads(FIXTURE.read_text());after=json.loads(FIXTURE.with_name('public-current-20261015-after.json').read_text())
        now=m.parse_time(after['checkedAt'],'now');self.run_data(before,BASE)
        self.run_data(after,now);self.run_data(after,now)
        resets=[a for a in self.calls if a.kind=='reset_recorded']
        self.assertEqual(len(resets),1);self.assertEqual(resets[0].key,r.identity('tibo-reset-2110618732666257557'))
        self.assertIn('2026-10-15 14:28',resets[0].text);self.assertIn('完成公告时间',resets[0].text)
        self.assertEqual(self.store.state['last_sent_at'],m.iso(BASE))
    def test_first_observation_recent_event_is_baseline_not_history_blast(self):
        now=BASE+timedelta(hours=1);self.run_data(self.changed(now),now)
        self.assertEqual(self.calls,[]);self.assertTrue(all(e['status']=='baseline' for e in self.store.state['reset_events']['records'].values()))
    def test_no_threshold_needed_new_event_sends_once_across_restart(self):
        self.baseline();now=BASE+timedelta(hours=1);raw=self.changed(now,p=0)
        result=self.run_data(raw,now);self.assertEqual(result['alerts'][0]['status'],'sent');self.assertEqual(self.calls[0].kind,'reset_recorded')
        self.assertIn('不是本人账户',self.calls[0].text);self.assertIn('完成公告时间',self.calls[0].text)
        self.run_data(self.changed(now+timedelta(minutes=1),p=.01),now+timedelta(minutes=1));self.assertEqual(len(self.calls),1)
    def test_forecast_cooldown_does_not_swallow_reset(self):
        self.run_data(self.data(p=.9));now=BASE+timedelta(hours=1);result=self.run_data(self.changed(now,p=.9),now)
        self.assertEqual([a.kind for a in self.calls],['forecast24h','reset_recorded'])
        self.assertEqual(result['alerts'][0]['status'],'cooldown_suppressed')
    def test_reset_does_not_start_forecast_cooldown(self):
        self.baseline();now=BASE+timedelta(hours=1);raw=self.changed(now);self.run_data(raw,now)
        self.assertIsNone(self.store.state['last_sent_at'])
        raw['viewModel']['probability24h']=.9;self.run_data(raw,now);self.assertEqual([a.kind for a in self.calls],['reset_recorded','forecast24h'])
    def test_probability_drop_notice_banked_and_regular_are_not_completed_events(self):
        self.baseline();now=BASE+timedelta(hours=1)
        for kind in ('banked_distribution','regular_completed','reference','official_notice'):
            raw=self.changed(now,p=0);raw['viewModel']['recentHistory'][0]['recordKind']=kind;self.run_data(raw,now)
        self.assertEqual(self.calls,[])
    def test_missing_history_does_not_initialize_cursor(self):
        raw=self.data();del raw['viewModel']['recentHistory'];self.run_data(raw)
        self.assertNotIn('reset_events',self.store.state)
    def test_old_state_alerts_and_installation_records_preserved(self):
        old={'version':1,'alerts':{'forecast:'+'a'*40:{'status':'sent','attempted_at':m.iso(BASE)}},'last_sent_at':m.iso(BASE),
             'acceptance_tests':{'b'*32:{'status':'sent','attempted_at':m.iso(BASE)}}}
        self.store=MemoryStore(old);self.baseline()
        for name in old:self.assertEqual(self.store.state[name],old[name])
    def test_dry_run_never_persists_baseline_or_queue(self):
        self.run_data(send=False);self.assertEqual(self.store.writes,[])
        self.baseline();before=copy.deepcopy(self.store.state);now=BASE+timedelta(hours=1)
        result=self.run_data(self.changed(now),now,send=False);self.assertEqual(result['alerts'][0]['status'],'would_alert');self.assertEqual(self.store.state,before)
    def test_queued_write_failure_never_calls_sender(self):
        self.baseline();self.store.fail_at=len(self.store.writes)+1;now=BASE+timedelta(hours=1)
        with self.assertRaises(m.StateError):self.run_data(self.changed(now),now)
        self.assertEqual(self.calls,[])
    def test_lost_queue_ack_can_resume_first_delivery_safely(self):
        self.baseline();self.store.lose_at=len(self.store.writes)+1;now=BASE+timedelta(hours=1)
        with self.assertRaises(m.StateError):self.run_data(self.changed(now),now)
        self.assertEqual(self.calls,[]);self.store.lose_at=None;self.run_data(self.changed(now),now);self.assertEqual(len(self.calls),1)
    def test_pending_write_failure_never_sends_and_queue_survives(self):
        self.baseline();self.store.fail_at=len(self.store.writes)+2;now=BASE+timedelta(hours=1)
        with self.assertRaises(m.StateError):self.run_data(self.changed(now),now)
        self.assertEqual(self.calls,[]);self.assertIn('queued',{e['status'] for e in self.store.state['reset_events']['records'].values()})
    def test_lost_pending_ack_never_sends_or_retries(self):
        self.baseline();self.store.lose_at=len(self.store.writes)+2;now=BASE+timedelta(hours=1)
        with self.assertRaises(m.StateError):self.run_data(self.changed(now),now)
        self.assertEqual(self.calls,[]);self.store.lose_at=None
        self.assertEqual(self.run_data(self.changed(now),now)['status'],'blocked_unresolved_delivery')
        self.assertEqual(self.calls,[])
    def test_multiple_new_records_queue_and_send_in_time_order_once(self):
        self.baseline();now=BASE+timedelta(hours=1);raw=self.changed(now)
        second=self.event(now-timedelta(minutes=1),number=2);raw['viewModel']['recentHistory'].append(second)
        self.run_data(raw,now);self.assertEqual([a.key for a in self.calls],[r.identity(second['key']),r.identity('test-confirmed-1')])
        self.run_data(raw,now);self.assertEqual(len(self.calls),2)
    def test_installation_uncertainty_blocks_both_types_and_keeps_event_queued(self):
        self.baseline();self.store.state['acceptance_tests']={'e'*32:{'status':'unknown','attempted_at':m.iso(BASE)}}
        now=BASE+timedelta(hours=1);result=self.run_data(self.changed(now,p=.9),now)
        self.assertEqual([a['status'] for a in result['alerts']],['blocked_unresolved_delivery','blocked_unresolved_delivery'])
        self.assertEqual(self.calls,[]);self.assertIn('queued',{e['status'] for e in self.store.state['reset_events']['records'].values()})
    def test_crash_after_pending_prevents_replay(self):
        self.baseline();now=BASE+timedelta(hours=1)
        def crash(*unused):raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):self.run_data(self.changed(now),now,sender=crash)
        self.assertEqual(self.run_data(self.changed(now),now)['status'],'blocked_unresolved_delivery');self.assertEqual(self.calls,[])
    def test_send_failure_and_final_write_failure_do_not_retry(self):
        for final_failure in (False,True):
            self.store=MemoryStore();self.calls=[];self.baseline();now=BASE+timedelta(hours=1)
            if final_failure:self.store.fail_at=len(self.store.writes)+3
            def sender(config,alert):
                self.calls.append(alert)
                if not final_failure:raise TimeoutError()
                return 'receipt'
            with self.assertRaises((m.SendUnknown,m.StateError)):self.run_data(self.changed(now),now,sender=sender)
            self.store.fail_at=None;self.run_data(self.changed(now),now);self.assertEqual(len(self.calls),1)
    def test_forecast_uncertainty_does_not_swallow_distinct_reset(self):
        self.baseline();self.store.state['alerts']['forecast:'+'c'*40]={'status':'unknown','attempted_at':m.iso(BASE)}
        now=BASE+timedelta(hours=1);self.run_data(self.changed(now),now);self.assertEqual([a.kind for a in self.calls],['reset_recorded'])
    def test_reset_uncertainty_does_not_swallow_forecast(self):
        self.baseline();now=BASE+timedelta(hours=1)
        with self.assertRaises(KeyboardInterrupt):self.run_data(self.changed(now),now,sender=lambda *a:(_ for _ in ()).throw(KeyboardInterrupt()))
        self.run_data(self.changed(now,p=.9),now);self.assertEqual([a.kind for a in self.calls],['forecast24h'])
    def test_rollback_and_same_timestamp_mutation_fail_closed(self):
        self.baseline();before=copy.deepcopy(self.store.state)
        raw=self.changed(BASE);result=self.run_data(raw,BASE);self.assertEqual(result['reset_events']['status'],'same_timestamp_history_revision')
        older=BASE-timedelta(minutes=1);result=self.run_data(self.changed(older),older);self.assertEqual(result['reset_events']['status'],'source_rollback')
        self.assertEqual(self.store.state,before);self.assertEqual(self.calls,[])
    def test_key_time_and_display_edits_do_not_resend(self):
        self.baseline();now=BASE+timedelta(hours=1);raw=self.changed(now);self.run_data(raw,now)
        for mode in ('time','key','title'):
            now+=timedelta(minutes=1);raw['checkedAt']=raw['dataHealth']['generatedAt']=m.iso(now)
            if mode=='time':raw['viewModel']['recentHistory'][0]['resetAt']=m.iso(now-timedelta(minutes=1))
            if mode=='key':raw['viewModel']['recentHistory'][0]['key']='renamed-key'
            if mode=='title':raw['viewModel']['recentHistory'][0]['title']='edited display only'
            self.run_data(raw,now)
        self.assertEqual(len(self.calls),1)
    def test_historical_backfill_and_expired_event_never_notify(self):
        self.baseline();now=BASE+timedelta(hours=1);raw=self.changed(now)
        raw['viewModel']['recentHistory'][0]['resetAt']='2026-10-12T12:00:00Z';self.run_data(raw,now)
        now+=timedelta(days=3);raw=self.changed(now,number=2);raw['viewModel']['recentHistory'][0]['resetAt']=m.iso(now-timedelta(days=2));self.run_data(raw,now)
        self.assertEqual(self.calls,[])
    def test_future_event_is_deferred_not_baselined_as_completed(self):
        self.baseline();now=BASE+timedelta(hours=1);raw=self.changed(now);raw['viewModel']['recentHistory'][0]['resetAt']=m.iso(now+timedelta(minutes=1))
        result=self.run_data(raw,now);self.assertEqual(result['reset_events']['future_records_ignored'],1);self.assertEqual(self.calls,[])
        now+=timedelta(minutes=2);raw['checkedAt']=raw['dataHealth']['generatedAt']=m.iso(now);self.run_data(raw,now);self.assertEqual(len(self.calls),1)
    def test_bad_source_fields_never_send_reset(self):
        self.baseline();now=BASE+timedelta(hours=1)
        for override in ({'resetAt':'no-zone'},{'key':'../unsafe'},{'source':'https://evil.invalid/post/123'},{'executionTimePrecision':'unknown'},{'executionTimePrecision':[]},{'source':'https://[invalid/status/12345678'}):
            raw=self.changed(now);raw['viewModel']['recentHistory'][0].update(override)
            self.assertIn('reset_source_error',self.run_data(raw,now))
        self.assertEqual(self.calls,[])
    def test_ineligible_provenance_and_stale_health_do_not_send(self):
        self.baseline();now=BASE+timedelta(hours=1);raw=self.changed(now);raw['viewModel']['recentHistory'][0]['sourceKind']='community_observation';self.run_data(raw,now)
        raw=self.changed(now);raw['dataHealth']['stale']=True
        with self.assertRaises(m.MonitorError):self.run_data(raw,now)
        self.assertEqual(self.calls,[])
    def test_corrupt_ledger_refuses_reset_instead_of_clearing(self):
        self.baseline();original=copy.deepcopy(self.store.state)
        for malformed in ({},{'status':[]},{'status':'queued','source_key':'event','source':'https://[invalid'}):
            self.store.state=copy.deepcopy(original);self.store.state['reset_events']['records']['bad']=malformed
            with self.assertRaises(m.StateError):self.run_data()
    def test_queued_event_removed_or_revised_by_source_is_not_sent(self):
        self.baseline();now=BASE+timedelta(hours=1);self.store.lose_at=len(self.store.writes)+1
        with self.assertRaises(m.StateError):self.run_data(self.changed(now),now)
        self.store.lose_at=None;now+=timedelta(minutes=1);self.run_data(self.data(now),now);self.assertEqual(self.calls,[])
        raw=self.changed(now);self.run_data(raw,now);self.assertEqual(self.calls,[])  # same key now has revised time

if __name__=='__main__':unittest.main()
