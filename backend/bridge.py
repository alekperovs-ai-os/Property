#!/usr/bin/env python3
"""Local GREEN-API campaign queue. Python 3.10+, no dependencies."""
import argparse, csv, fcntl, getpass, json, os, re, sqlite3, sys, time, zipfile
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.request import Request, urlopen, HTTPRedirectHandler, build_opener
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

ROOT = Path(os.environ.get('GREENAPI_DATA_DIR', Path(__file__).resolve().parent)).resolve()
ROOT.mkdir(parents=True, exist_ok=True)
TZ = ZoneInfo('Asia/Dubai')

def now(): return datetime.now(TZ)
def config():
    c = json.loads((ROOT/'secrets.json').read_text())
    u = urlparse(c['api_url'])
    if u.scheme != 'https' or not re.fullmatch(r'(?:[a-z0-9-]+\.)*(?:green-api|greenapi)\.com', u.hostname or '') or u.path not in ('', '/') or u.query or u.username or u.port:
        raise ValueError('api_url must be an HTTPS GREEN-API host without path')
    if not re.fullmatch(r'\d+', c['instance']) or not re.fullmatch(r'[A-Za-z0-9_-]+', c['token']):
        raise ValueError('Invalid instance or token')
    return c

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None

def api(method, data=None):
    c = config()
    url = f"{c['api_url'].rstrip('/')}/waInstance{c['instance']}/{method}/{c['token']}"
    req = Request(url, data=None if data is None else json.dumps(data).encode(), headers={'Content-Type':'application/json'})
    try:
        with build_opener(NoRedirect).open(req, timeout=30) as r: return json.load(r)
    except Exception:
        # Never print the exception: urllib errors can contain the credential URL.
        raise RuntimeError('GREEN-API request failed; credentials and URL redacted') from None

def db():
    os.umask(0o077)
    d = sqlite3.connect(ROOT/'queue.sqlite', timeout=30)
    d.row_factory = sqlite3.Row
    d.execute('PRAGMA journal_mode=WAL')
    d.execute('PRAGMA busy_timeout=30000')
    d.executescript('''
    CREATE TABLE IF NOT EXISTS campaigns(id TEXT PRIMARY KEY, state TEXT, daily INTEGER, start TEXT, end TEXT);
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, campaign TEXT, phone TEXT, name TEXT, body TEXT, state TEXT DEFAULT 'pending', attempted TEXT, provider_id TEXT, UNIQUE(campaign,phone));
    CREATE TABLE IF NOT EXISTS blocked(phone TEXT PRIMARY KEY);
    ''')
    return d

def phone(value):
    s = str(value).strip()
    if not re.fullmatch(r'\+?[\d ()-]+', s): raise ValueError('Use international phone numbers as text')
    s = re.sub(r'\D','',s)
    if s.startswith('00'): s = s[2:]
    if not re.fullmatch(r'[1-9]\d{7,14}', s): raise ValueError('Invalid international phone number')
    return s

def rows(path):
    p = Path(path)
    if p.suffix.lower() == '.csv':
        with p.open(encoding='utf-8-sig',newline='') as f:
            sample=f.read(4096); f.seek(0)
            try: dialect=csv.Sniffer().sniff(sample,delimiters=',;\t')
            except csv.Error: dialect=csv.excel
            yield from csv.DictReader(f,dialect=dialect)
    elif p.suffix.lower() == '.xlsx':
        ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        with zipfile.ZipFile(p) as z:
            strings=[]
            if 'xl/sharedStrings.xml' in z.namelist():
                strings=[''.join(e.itertext()) for e in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('s:si',ns)]
            wb=ET.fromstring(z.read('xl/workbook.xml'))
            sheet=wb.find('s:sheets/s:sheet',ns)
            rid=sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
            rels=ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
            target=next(e.attrib['Target'] for e in rels if e.attrib['Id']==rid)
            target=target.lstrip('/') if target.startswith('/') else 'xl/'+target
            header=None
            for row in ET.fromstring(z.read(target)).findall('.//s:sheetData/s:row',ns):
                values={}
                for cell in row:
                    col=re.match(r'[A-Z]+',cell.attrib['r']).group()
                    v=cell.find('s:v',ns); value=v.text if v is not None else ''
                    if cell.find('s:f',ns) is not None: raise ValueError('Export formulas as values before import')
                    if cell.attrib.get('t')=='s': value=strings[int(value)]
                    if cell.attrib.get('t')=='inlineStr': value=''.join(cell.find('s:is',ns).itertext())
                    values[col]=value or ''
                if not any(values.values()): continue
                if header is None: header=values; continue
                yield {name:values.get(col,'') for col,name in header.items()}
    else: raise ValueError('Supported files: .xlsx and .csv')

def create(args):
    if not 1<=args.daily<=30 or not 1<=args.days<=366: raise ValueError('daily: 1–30; days: 1–366')
    start=datetime.fromisoformat(args.start).date() if args.start else now().date()
    template=Path(args.text).read_text(encoding='utf-8')
    batch={}
    for i,r in enumerate(rows(args.file),2):
        r={str(k).strip().lower():str(v or '').strip() for k,v in r.items()}
        raw=r.get('phone') or r.get('телефон')
        if not raw: raise ValueError(f'Row {i}: missing phone / телефон')
        number=phone(raw); name=r.get('name') or r.get('имя','')
        body=template.replace('{name}',name)
        if not body.strip() or len(body)>20000: raise ValueError(f'Row {i}: message must contain 1–20000 characters')
        if number in batch and batch[number]!=(name,body): raise ValueError(f'Row {i}: conflicting duplicate phone')
        batch[number]=(name,body)
    if not batch: raise ValueError('Empty contact list')
    d=db()
    with d:
        d.execute('INSERT INTO campaigns VALUES(?,?,?,?,?)',(args.id,'draft',args.daily,str(start),str(start+timedelta(days=args.days-1))))
        d.executemany('INSERT INTO messages(campaign,phone,name,body) VALUES(?,?,?,?)',[(args.id,p,n,b) for p,(n,b) in batch.items()])
    return {'campaign':args.id,'contacts':len(batch),'capacity':args.daily*args.days,'state':'draft','preview':[(p,n,b) for p,(n,b) in list(batch.items())[:3]]}

def tick(campaign=None, immediate=False):
    c=config(); d=db(); t=now(); day=t.date().isoformat()
    # Global account cap and minimum spacing, including uncertain attempts.
    limit=int(c.get('daily_limit',30))
    if not 1<=limit<=30: raise ValueError('daily_limit must be 1–30')
    if not immediate and not 9<=t.hour<18: return {'state':'outside_window'}
    count=d.execute('SELECT count(*) FROM messages WHERE substr(attempted,1,10)=?',(day,)).fetchone()[0]
    if count>=limit: return {'state':'daily_limit'}
    last=d.execute('SELECT max(attempted) FROM messages').fetchone()[0]
    if last and (t-datetime.fromisoformat(last)).total_seconds()<max(60,int(c.get('interval_seconds',600))): return {'state':'interval'}
    row=d.execute('''SELECT m.* FROM messages m JOIN campaigns c ON c.id=m.campaign
    WHERE (? IS NULL OR c.id=?) AND c.state='active' AND c.start<=? AND c.end>=? AND m.state='pending'
    AND m.phone NOT IN (SELECT phone FROM blocked)
    AND (SELECT count(*) FROM messages x WHERE x.campaign=c.id AND substr(x.attempted,1,10)=?)<c.daily
    ORDER BY m.id LIMIT 1''',(campaign,campaign,day,day,day)).fetchone()
    if not row: return {'state':'idle'}
    # A crash must pause the entire campaign, not just skip the uncertain contact.
    if d.execute("SELECT 1 FROM messages WHERE campaign=? AND state='uncertain'",(row['campaign'],)).fetchone():
        with d: d.execute("UPDATE campaigns SET state='paused' WHERE id=?",(row['campaign'],))
        return {'state':'uncertain_campaign_paused'}
    if api('getStateInstance').get('stateInstance')!='authorized': return {'state':'instance_not_authorized'}
    # Commit before network. Crash/timeout remains uncertain and is never auto-retried.
    with d:
        updated=d.execute("UPDATE messages SET state='uncertain',attempted=? WHERE id=? AND state='pending' AND EXISTS(SELECT 1 FROM campaigns WHERE id=? AND state='active') AND phone NOT IN (SELECT phone FROM blocked)",(t.isoformat(),row['id'],row['campaign']))
        if updated.rowcount!=1: return {'state':'cancelled_before_send'}
    try:
        result=api('sendMessage',{'chatId':row['phone']+'@c.us','message':row['body']})
        if not result.get('idMessage'): raise RuntimeError('Missing message identifier')
        with d: d.execute("UPDATE messages SET state='accepted',provider_id=? WHERE id=?",(result['idMessage'],row['id']))
        return {'state':'accepted','message':row['id'],'provider_id':result['idMessage']}
    except Exception:
        with d: d.execute("UPDATE campaigns SET state='paused' WHERE id=?",(row['campaign'],))
        return {'state':'uncertain','message':row['id'],'campaign_paused':True}

def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest='cmd',required=True)
    sub.add_parser('configure'); sub.add_parser('check'); sub.add_parser('tick'); sub.add_parser('status')
    c=sub.add_parser('create')
    for key in ('id','file','text'): c.add_argument('--'+key,required=True)
    c.add_argument('--daily',type=int,default=30); c.add_argument('--days',type=int,default=7); c.add_argument('--start')
    for cmd in ('activate','pause','preview'):
        s=sub.add_parser(cmd); s.add_argument('id')
    s=sub.add_parser('block'); s.add_argument('phone')
    a=p.parse_args()
    if a.cmd=='configure':
        c={'api_url':input('apiUrl: ').strip(),'instance':input('idInstance: ').strip(),'token':getpass.getpass('apiTokenInstance (hidden): '),'daily_limit':30,'interval_seconds':600}
        (ROOT/'secrets.json').write_text(json.dumps(c)); os.chmod(ROOT/'secrets.json',0o600)
        config(); out={'configured':True}
    elif a.cmd=='check': out=api('getStateInstance')
    elif a.cmd=='create': out=create(a)
    elif a.cmd=='tick':
        with (ROOT/'worker.lock').open('w') as lock:
            try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: return
            out=tick()
    else:
        d=db()
        if a.cmd in ('activate','pause'):
            with d:
                cur=d.execute('UPDATE campaigns SET state=? WHERE id=?',('active' if a.cmd=='activate' else 'paused',a.id))
                if not cur.rowcount: raise ValueError('Unknown campaign')
            out={'campaign':a.id,'state':'active' if a.cmd=='activate' else 'paused'}
        elif a.cmd=='block':
            with d: d.execute('INSERT OR IGNORE INTO blocked VALUES(?)',(phone(a.phone),))
            out={'blocked':True}
        elif a.cmd=='preview': out=[dict(r) for r in d.execute('SELECT id,phone,name,body,state FROM messages WHERE campaign=?',(a.id,))]
        else: out=[dict(r) for r in d.execute('SELECT c.*,m.state AS message_state,count(m.id) AS count FROM campaigns c LEFT JOIN messages m ON m.campaign=c.id GROUP BY c.id,m.state')]
    print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=='__main__':
    try: main()
    except Exception as e:
        print(json.dumps({'error':str(e) if isinstance(e,(ValueError,RuntimeError,FileNotFoundError,sqlite3.IntegrityError)) else type(e).__name__},ensure_ascii=False),file=sys.stderr); sys.exit(1)
