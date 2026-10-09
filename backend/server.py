"""Authenticated dashboard API. Bind behind an HTTPS reverse proxy."""
import fcntl,hmac,json,os,tempfile,types
from pathlib import Path
from datetime import datetime
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlsplit
import bridge
PUBLIC=Path(__file__).resolve().parent.parent/'public'
TOKEN=os.environ.get('DASHBOARD_TOKEN','')
ORIGIN=os.environ.get('DASHBOARD_ORIGIN','https://property-blue-zeta.vercel.app')

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*_): pass
    def reply(self,code,value):
        body=json.dumps(value,ensure_ascii=False).encode()
        self.send_response(code); self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        if self.headers.get('Origin')==ORIGIN:
            self.send_header('Access-Control-Allow-Origin',ORIGIN); self.send_header('Vary','Origin')
        self.end_headers(); self.wfile.write(body)
    def auth(self):
        if len(TOKEN)<32:
            self.reply(503,{'error':'Server access token is not configured'}); return False
        if not hmac.compare_digest(self.headers.get('Authorization',''), 'Bearer '+TOKEN):
            self.reply(401,{'error':'Войдите с ключом доступа к дашборду'}); return False
        return True
    def do_OPTIONS(self):
        if self.headers.get('Origin')!=ORIGIN: return self.reply(403,{'error':'Origin rejected'})
        self.send_response(204);self.send_header('Access-Control-Allow-Origin',ORIGIN)
        self.send_header('Access-Control-Allow-Methods','GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers','Authorization, Content-Type')
        self.send_header('Vary','Origin');self.end_headers()
    def do_GET(self):
        path=urlsplit(self.path).path
        if path=='/healthz': return self.reply(200,{'ok':True,'worker_enabled':os.environ.get('WORKER_ENABLED')=='1'})
        if path in ('/','/index.html'):
            body=(PUBLIC/'index.html').read_bytes();self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Cache-Control','no-store')
            self.end_headers();return self.wfile.write(body)
        if path!='/api/status': return self.reply(404,{'error':'Not found'})
        if not self.auth(): return
        try:
            with bridge.db() as d:
                messages=[dict(r) for r in d.execute('SELECT * FROM messages ORDER BY id DESC LIMIT 500')]
                campaigns=[dict(r) for r in d.execute('SELECT * FROM campaigns ORDER BY start DESC')]
                day=bridge.now().date().isoformat()
                accepted=d.execute("SELECT count(*) FROM messages WHERE state='accepted' AND substr(attempted,1,10)=?",(day,)).fetchone()[0]
                pending=d.execute("SELECT count(*) FROM messages WHERE state='pending'").fetchone()[0]
            config=bridge.config()
            return self.reply(200,{'date':day,'timezone':'Asia/Dubai','accepted_today':accepted,'pending':pending,'limit':int(config.get('daily_limit',30)), 'messages':messages,'campaigns':campaigns,'worker_enabled':os.environ.get('WORKER_ENABLED')=='1','unread':None,'replies':None})
        except Exception: return self.reply(503,{'error':'Хранилище или интеграция не настроены'})
    def do_POST(self):
        if not self.auth(): return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=1_000_000: return self.reply(413,{'error':'Invalid request size'})
            data=json.loads(self.rfile.read(size));path=urlsplit(self.path).path
            with (bridge.ROOT/'worker.lock').open('a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX)
                if path=='/api/campaigns':
                    contacts=data.get('contacts',[])
                    if not isinstance(contacts,list) or not 1<=len(contacts)<=10000: raise ValueError('Нужен список 1–10000 контактов')
                    import csv
                    with tempfile.TemporaryDirectory() as tmp:
                        p=Path(tmp)
                        with (p/'contacts.csv').open('w',newline='') as f:
                            writer=csv.DictWriter(f,fieldnames=['phone','name']);writer.writeheader()
                            for c in contacts:writer.writerow({'phone':c['phone'],'name':c.get('name','')})
                        (p/'text.txt').write_text(data['text'])
                        args=types.SimpleNamespace(id=data['id'],file=str(p/'contacts.csv'),text=str(p/'text.txt'),daily=int(data.get('daily',30)),days=int(data.get('days',7)),start=data.get('start'))
                        result=bridge.create(args)
                        # Return the complete preview; activation is a separate explicit request.
                        with bridge.db() as d: result['preview']=[dict(r) for r in d.execute('SELECT name,phone,body,state FROM messages WHERE campaign=?',(args.id,))]
                        return self.reply(201,result)
                if path=='/api/campaign-state':
                    state=data['state'];campaign=data['id']
                    if state not in ('active','paused'): raise ValueError('Invalid state')
                    with bridge.db() as d:
                        if state=='active':
                            if data.get('confirm') is not True: raise ValueError('Подтвердите текст и всех получателей')
                            if d.execute("SELECT 1 FROM messages WHERE campaign=? AND state='uncertain'",(campaign,)).fetchone(): raise ValueError('Есть неопределённая попытка; проверьте её перед запуском')
                        cur=d.execute('UPDATE campaigns SET state=? WHERE id=?',(state,campaign))
                        if cur.rowcount!=1: raise ValueError('Кампания не найдена')
                    return self.reply(200,{'id':campaign,'state':state})
                if path=='/api/pause-all':
                    with bridge.db() as d: d.execute("UPDATE campaigns SET state='paused' WHERE state='active'")
                    return self.reply(200,{'paused':True})
                if path=='/api/message-status':
                    with bridge.db() as d: row=d.execute('SELECT phone,provider_id FROM messages WHERE id=?',(int(data['id']),)).fetchone()
                    if not row or not row['provider_id']: raise ValueError('Нет идентификатора отправленного сообщения')
                    result=bridge.api('getMessage',{'chatId':row['phone']+'@c.us','idMessage':row['provider_id']})
                    return self.reply(200,{'id':data['id'],'provider_status':result.get('statusMessage','unknown')})
            return self.reply(404,{'error':'Not found'})
        except (ValueError,KeyError,TypeError): return self.reply(400,{'error':'Проверьте запрос, получателей и состояние кампании'})
        except Exception: return self.reply(503,{'error':'Операция не выполнена; детали скрыты'})

if __name__=='__main__':
    os.umask(0o077)
    ThreadingHTTPServer(('0.0.0.0',int(os.environ.get('PORT',8080))),Handler).serve_forever()
