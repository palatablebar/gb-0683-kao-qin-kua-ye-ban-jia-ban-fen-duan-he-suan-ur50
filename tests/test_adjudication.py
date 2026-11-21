import base64
from copy import deepcopy
from decimal import Decimal

from app import store
from app.workflow import run
from app.modules.m5_calculate.rules import price
from app.modules.m6_reconcile.compare import balance
from app.modules.m7_archive.service import execute as seal

HEADER='id,employee,shift_day,version,punch,at,shift_start,shift_end,break_start,break_end,hourly_rate,paid'

def csv_of(*rows):
    return ('\n'.join([HEADER]+[','.join(map(str,r)) for r in rows])+'\n').encode()

def shift(day='2026-10-05',clock_in='2026-10-05T22:00:00+08:00',clock_out='2026-10-06T06:00:00+08:00',paid='0',version='1'):
    return [(1,'E100',day,version,'IN',clock_in,'22:00','06:00','02:00','03:00','20',paid),
            (2,'E100',day,version,'OUT',clock_out,'22:00','06:00','02:00','03:00','20','0')]

EARLY_CSV=csv_of(*shift(clock_in='2026-10-05T20:00:00+08:00'))
QUOTA_CSV=csv_of(*shift(clock_in='2026-10-05T16:00:00+08:00'))
LATE_CSV=csv_of(*shift(clock_out='2026-10-06T07:00:00+08:00'))
BOTH_CSV=csv_of(*shift(clock_in='2026-10-05T20:00:00+08:00',clock_out='2026-10-06T07:00:00+08:00',paid='200'))
HOLIDAY_CSV=csv_of(*shift(day='2026-10-01',clock_in='2026-10-01T21:00:00+08:00',clock_out='2026-10-02T06:00:00+08:00'))

def segment(minutes,phase=None,day='2026-10-05',start=None):
    s={'id':'S','employee':'E','shift_day':day,'calendar_day':day,'start':start or day+'T09:00:00+08:00','end':day+'T18:00:00+08:00','minutes':minutes,'rate':'20','line':2}
    if phase:s['phase']=phase
    return s

# 两段时间形成同一份可付款结果：阶段标注
def test_segments_carry_phase():
    segments=run(EARLY_CSV,{},4)['segments']
    assert [(s['phase'],s['minutes']) for s in segments]==[('EARLY',120),('PLANNED',120),('PLANNED',120),('PLANNED',180)]

def test_extended_segment_labeled():
    phases={s['phase'] for s in run(LATE_CSV,{},4)['segments']}
    assert phases=={'PLANNED','EXTENDED'}

# 裁决一：提前段不占用晚间正常额度
def test_early_minutes_exempt_from_quota():
    segs=[segment(300,'EARLY',start='2026-10-05T06:00:00+08:00'),segment(300,'PLANNED',start='2026-10-05T12:00:00+08:00')]
    result=price(segs,set())
    assert all(r['code']!='OVERTIME' for r in result) and sum(r['minutes'] for r in result)==600

def test_early_arrival_never_paid_as_overtime():
    state=run(QUOTA_CSV,{})
    assert state['business']['total']=='260.00'
    assert all(r['code']!='OVERTIME' for r in state['calculations'])
    assert state['business']['by_code']['EARLY']=={'minutes':360,'amount':'120.00'}

def test_early_segment_base_rate():
    state=run(EARLY_CSV,{})
    early=[r for r in state['calculations'] if r['code']=='EARLY']
    assert len(early)==1 and early[0]['multiplier']=='1' and early[0]['amount']=='40.00'
    assert state['business']['total']=='180.00'

# 裁决二：交接延误的延时段计加班
def test_handover_delay_is_overtime():
    state=run(LATE_CSV,{})
    overtime=[r for r in state['calculations'] if r['code']=='OVERTIME']
    assert len(overtime)==1 and overtime[0]['phase']=='EXTENDED'
    assert overtime[0]['minutes']==60 and overtime[0]['multiplier']=='1.5' and overtime[0]['amount']=='30.00'
    assert state['business']['total']=='170.00'

def test_extended_minutes_exempt_from_quota():
    result=price([segment(30,'EXTENDED')],set())
    assert [(r['minutes'],r['multiplier'],r['code']) for r in result]==[(30,'1.5','OVERTIME')]

# 同一份可付款结果：实际工作、跨日倍率、正常额度、应付逐项对应
def test_single_payable_result_item_by_item():
    state=run(BOTH_CSV,{})
    items=state['calculations']
    assert [(r['phase'],r['code'],r['minutes'],r['multiplier'],r['amount']) for r in items]==[
        ('EARLY','EARLY',120,'1','40.00'),
        ('PLANNED','REGULAR',120,'1','40.00'),
        ('PLANNED','REGULAR',120,'1','40.00'),
        ('PLANNED','REGULAR',180,'1','60.00'),
        ('EXTENDED','OVERTIME',60,'1.5','30.00')]
    assert sum(r['minutes'] for r in items)==600==state['business']['minutes']
    assert sum(Decimal(r['amount']) for r in items)==Decimal('210.00')
    assert state['business']['total']=='210.00' and state['business']['paid']=='200' and state['business']['difference']=='10.00'
    assert state['business']['by_phase']=={'EARLY':{'minutes':120,'amount':'40.00'},'EXTENDED':{'minutes':60,'amount':'30.00'},'PLANNED':{'minutes':420,'amount':'140.00'}}
    assert state['reconcile']['balanced'] is True and state['archive']['ready'] is True

# 跨日倍率覆盖所有阶段
def test_early_on_holiday_keeps_day_multiplier():
    state=run(HOLIDAY_CSV,{})
    early=[r for r in state['calculations'] if r['phase']=='EARLY']
    assert len(early)==1 and early[0]['code']=='HOLIDAY' and early[0]['multiplier']=='3' and early[0]['amount']=='60.00'

def test_missing_phase_defaults_to_planned():
    result=price([segment(540)],set())
    assert [(r['minutes'],r['multiplier']) for r in result]==[(480,'1'),(60,'1.5')]

# 对账不符时停止封存
def test_reconcile_stops_sealing_on_minutes_mismatch():
    state=run(BOTH_CSV,{});tampered=deepcopy(state)
    tampered['calculations'][0]['minutes']+=1
    result=balance(tampered)
    assert result['balanced'] is False and result['checks']['minutes'] is False
    tampered['reconcile']=result
    assert seal(tampered)['archive']['ready'] is False

def test_reconcile_stops_sealing_on_amount_mismatch():
    state=run(BOTH_CSV,{});tampered=deepcopy(state)
    tampered['business']['total']='999.00'
    result=balance(tampered)
    assert result['balanced'] is False and result['checks']['amounts'] is False and result['checks']['difference'] is False

def test_export_blocked_when_unbalanced(client):
    state=run(BOTH_CSV,{});state['reconcile']['balanced']=False
    store.save('tampered-run',state)
    r=client.get('/api/archive/tampered-run/export.csv')
    assert r.status_code==422 and r.get_json()['code']=='UNBALANCED'

# 同一班重复执行不得新增付款
def test_rerun_same_shift_adds_no_payment(client):
    a=client.post('/api/runs',json={'csv':BOTH_CSV.decode()}).get_json()
    before=len(client.get('/api/runs').get_json())
    b=client.post('/api/runs',json={'csv':BOTH_CSV.decode()}).get_json()
    assert a['run_id']==b['run_id']
    assert len(a['calculations'])==len(b['calculations']) and a['business']['total']==b['business']['total']
    assert len(client.get('/api/runs').get_json())==before

# 补卡或排班更新后，先前付款依据失去效力并重新核算
def test_correction_supersedes_previous_basis(client):
    v1=csv_of(*shift())
    corrected=[list(r) for r in shift(clock_in='2026-10-05T20:00:00+08:00')]
    corrected[0][3]='2';corrected[1][3]='1'
    v2=csv_of(*corrected)
    a=client.post('/api/runs',json={'csv':v1.decode()}).get_json()
    b=client.post('/api/runs',json={'csv':v2.decode()}).get_json()
    assert a['run_id']!=b['run_id']
    history=client.get('/api/runs').get_json()
    assert history[0]['id']==b['run_id'] and history[0]['effective'] is True
    assert {h['id']:h['effective'] for h in history}[a['run_id']] is False
    assert any(r['code']=='EARLY' for r in b['calculations'])
    old=client.get('/api/runs/'+a['run_id']).get_json()
    assert all(r['code']!='EARLY' for r in old['calculations'])
    assert base64.b64decode(old['source_b64'])==v1

# 新版生成失败则恢复旧版，原始打卡可追回
def test_failed_regeneration_keeps_previous(client):
    good=client.post('/api/runs',json={'csv':BOTH_CSV.decode()}).get_json()
    assert client.post('/api/runs',json={'csv':'wrong\n1\n'}).status_code==422
    assert client.get('/api/runs').get_json()[0]['id']==good['run_id']
    page=client.get('/m5_calculate')
    assert page.status_code==200 and good['run_id'].encode() in page.data

def test_original_punches_recoverable():
    state=run(BOTH_CSV,{})
    assert base64.b64decode(state['source_b64'])==BOTH_CSV

# 工作页面与导出共同呈现
def test_page_and_export_present_same_result(client):
    state=client.post('/api/runs',json={'csv':BOTH_CSV.decode()}).get_json()
    page=client.get('/m5_calculate').data.decode()
    assert '提前' in page and '延时' in page and 'EARLY' in page and '210.00' in page
    export=client.get('/api/archive/'+state['run_id']+'/export.csv').data.decode('utf-8-sig')
    assert 'EARLY' in export and 'EXTENDED' in export and '210.00' in export
