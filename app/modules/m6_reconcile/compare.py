from decimal import Decimal

def balance(state):
    accepted=len(state['rows']);isolated=len(state['quarantine'])
    accounted=accepted+isolated+state['blank_count']+state['header_count']+state['duplicate_count']
    calculations=state.get('calculations',[]);segments=state.get('segments',[]);business=state.get('business',{})
    evidence_minutes=sum(r['minutes'] for r in calculations);worked_minutes=sum(r['minutes'] for r in segments)
    amounts=sum((Decimal(str(r['amount'])) for r in calculations),Decimal(0))
    total=Decimal(str(business.get('total','0')));paid=Decimal(str(business.get('paid','0')));difference=Decimal(str(business.get('difference','0')))
    # 四项独立核查，任一不符即停止封存：行数守恒、工时守恒、金额重算、差额一致。
    checks={'rows':accounted==state['input_count'],
        'minutes':evidence_minutes==worked_minutes==business.get('minutes'),
        'amounts':amounts==total,
        'difference':total-paid==difference}
    return {'input':state['input_count'],'accepted':accepted,'isolated':isolated,
        'blank':state['blank_count'],'headers':state['header_count'],'duplicates':state['duplicate_count'],
        'accounted':accounted,'checks':checks,'balanced':all(checks.values()),
        'business':business}
