"""Private audit snapshot and owner review. Does not send messages."""
import json, os, tempfile, uuid
from datetime import timedelta
import bridge

def snapshot():
    p=bridge.ROOT/'client-audit-summary.json'
    if not p.is_file():raise ValueError('Разбор ещё не загружен')
    data=json.loads(p.read_text())
    p=bridge.ROOT/'client-audit-review.json'
    data['review']=json.loads(p.read_text()) if p.is_file() else {}
    return data

def review(data):
    records={r['id']:r for r in snapshot()['records']}
    cid=data['id']; decision=data['decision']; body=data.get('text','')
    if cid not in records or decision not in ('include','skip','reset'):raise ValueError('Invalid review')
    if decision=='include' and records[cid]['category']!='candidate':raise ValueError('Сначала нужна дополнительная проверка чата')
    if not isinstance(body,str) or len(body)>20000:raise ValueError('Invalid text')
    p=bridge.ROOT/'client-audit-review.json'; saved=snapshot()['review']
    if decision=='reset':saved.pop(cid,None)
    else:saved[cid]={'decision':decision,'text':body,'updated':bridge.now().isoformat()}
    fd,tmp=tempfile.mkstemp(dir=bridge.ROOT,prefix='audit-review-')
    try:
        with os.fdopen(fd,'w') as f:json.dump(saved,f,ensure_ascii=False)
        os.replace(tmp,p)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    return {'ok':True}

def draft(data):
    if data.get('checked_replies') is not True:raise ValueError('Проверьте свежие ответы в WhatsApp')
    audit=snapshot(); chosen=[r for r in audit['records'] if audit['review'].get(r['id'],{}).get('decision')=='include' and not audit['review'].get(r['id'],{}).get('campaign')]
    if not 1<=len(chosen)<=30:raise ValueError('Выберите от 1 до 30 клиентов')
    batch=[]
    for r in chosen:
        if r['category']!='candidate':raise ValueError('Клиент требует дополнительной проверки')
        body=audit['review'][r['id']]['text'].strip()
        if not body:raise ValueError('Укажите сообщение каждому клиенту')
        batch.append((bridge.phone(r['phone']),r['name'],body))
    cid='followup-'+bridge.now().strftime('%Y%m%d')+'-'+uuid.uuid4().hex[:8]
    day=bridge.now().date()
    if bridge.now().hour>=18:day+=timedelta(days=1)
    with bridge.db() as d:
        for phone,name,body in batch:bridge.check_duplicate(d,phone,body)
        d.execute('INSERT INTO campaigns(id,state,daily,start,end) VALUES(?,?,?,?,?)',(cid,'draft',30,str(day),str(day+timedelta(days=6))))
        d.executemany('INSERT INTO messages(campaign,phone,name,body) VALUES(?,?,?,?)',[(cid,*b) for b in batch])
    return {'campaign':cid,'state':'draft','contacts':len(batch)}
