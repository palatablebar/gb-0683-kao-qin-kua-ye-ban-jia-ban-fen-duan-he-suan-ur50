from datetime import date
from decimal import Decimal,ROUND_HALF_UP
from collections import defaultdict

# 裁决：提前段(EARLY)为普通早到，按1倍计付，不占正常额度、不计加班；
# 计划段(PLANNED)按(员工,日历日)累计消耗480分钟正常额度，额度内1倍、超出1.5倍；
# 延时段(EXTENDED)为延时加班，按1.5倍计付，不占正常额度；
# 法定假日3倍、周末2倍按实际日历日覆盖所有阶段。
def parts(s,holidays,phase,normal_used):
    d=date.fromisoformat(s['calendar_day']);key=(s['employee'],s['calendar_day']);minutes=s['minutes']
    if s['calendar_day'] in holidays:return [(minutes,Decimal(3),'HOLIDAY')]
    if d.weekday()>=5:return [(minutes,Decimal(2),'REST')]
    if phase=='EARLY':return [(minutes,Decimal(1),'EARLY')]
    if phase=='EXTENDED':return [(minutes,Decimal('1.5'),'OVERTIME')]
    regular=min(minutes,max(0,480-normal_used[key]));normal_used[key]+=minutes
    return [(regular,Decimal(1),'REGULAR'),(minutes-regular,Decimal('1.5'),'OVERTIME')]

def price(segments,holidays):
    normal_used=defaultdict(int);out=[]
    for s in sorted(segments,key=lambda r:(r['start'],r['employee'])):
        rate=Decimal(str(s['rate']))
        for count,multiplier,code in parts(s,holidays,s.get('phase','PLANNED'),normal_used):
            if count:
                item=dict(s);item.update(minutes=count,multiplier=str(multiplier),code=code,amount=str((rate*count*multiplier/60).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)));out.append(item)
    return out

def summarize(calculations):
    # 汇总与明细同源同口径：按分类与按阶段双视角，分钟与金额逐项可回查。
    views=(defaultdict(lambda:{'minutes':0,'amount':Decimal(0)}),defaultdict(lambda:{'minutes':0,'amount':Decimal(0)}))
    for r in calculations:
        amount=Decimal(r['amount'])
        for bucket,key in ((views[0],r['code']),(views[1],r.get('phase','PLANNED'))):
            bucket[key]['minutes']+=r['minutes'];bucket[key]['amount']+=amount
    return [{k:{'minutes':v['minutes'],'amount':str(v['amount'])} for k,v in sorted(bucket.items())} for bucket in views]

def apply(state):
    holidays=set(state['parameters'].get('holidays',['2026-10-01']))
    calculations=price(state['segments'],holidays)
    total=sum((Decimal(r['amount']) for r in calculations),Decimal(0));paid=sum((Decimal(str(r['paid'])) for r in state['rows']),Decimal(0))
    by_code,by_phase=summarize(calculations)
    state['calculations']=calculations
    state['business']={'total':str(total),'paid':str(paid),'difference':str(total-paid),'minutes':sum(r['minutes'] for r in calculations),'unit':'CNY','by_code':by_code,'by_phase':by_phase};return state
