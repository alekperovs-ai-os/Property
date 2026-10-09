"""Authenticated dashboard API. Bind behind an HTTPS reverse proxy."""
import fcntl,hmac,json,os,tempfile,types
from pathlib import Path
from datetime import datetime
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlsplit,parse_qs
import bridge,admin,client_audit
from http.cookies import SimpleCookie
PUBLIC=Path(__file__).resolve().parent.parent/'public'
ORIGIN=os.environ.get('DASHBOARD_ORIGIN','https://property-blue-zeta.vercel.app')

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*_): pass
    def reply(self,code,value,cookie=None):
        body=json.dumps(value,ensure_ascii=False).encode()
        self.send_response(code); self.send_header('Content-Type','application/json; charset=utf-8')
        if cookie is not None:self.send_header('Set-Cookie',cookie)
        self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        if self.headers.get('Origin')==ORIGIN:
            self.send_header('Access-Control-Allow-Origin',ORIGIN); self.send_header('Vary','Origin')
        self.end_headers(); self.wfile.write(body)
    def session(self):
        try:
            cookies=SimpleCookie(self.headers.get('Cookie',''))
            return cookies['property_session'].value if 'property_session' in cookies else ''
        except Exception:return ''
    def auth(self):
        if not admin.authenticated(self.session()):
            self.reply(401,{'error':'Войдите с email и паролем'});return False
        return True
    def cookie(self,token,maxage=604800):
        return 'property_session='+token+'; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age='+str(maxage)
    def do_OPTIONS(self):
        if self.headers.get('Origin')!=ORIGIN: return self.reply(403,{'error':'Origin rejected'})
        self.send_response(204);self.send_header('Access-Control-Allow-Origin',ORIGIN)
        self.send_header('Access-Control-Allow-Methods','GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers','Authorization, Content-Type')
        self.send_header('Vary','Origin');self.end_headers()
    def do_GET(self):
        path=urlsplit(self.path).path
        if path.startswith('/setup/'):
            code=path.split('/setup/',1)[1]
            if not admin.setup_allowed(code):return self.reply(404,{'error':'Ссылка недействительна или аккаунт уже создан'})
            page=SETUP_PAGE.replace('__SETUP_TOKEN__',json.dumps(code)).encode()
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','no-referrer');self.end_headers();return self.wfile.write(page)
        if path=='/healthz': return self.reply(200,{'ok':True,'worker_enabled':os.environ.get('WORKER_ENABLED')=='1'})
        if path in ('/','/index.html'):
            body=(PUBLIC/'index.html').read_bytes();self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Cache-Control','no-store')
            self.end_headers();return self.wfile.write(body)
        if path=='/followups.html':
            if not admin.authenticated(self.session()):
                self.send_response(303)
                self.send_header('Location','index.html?next=followups')
                self.send_header('Cache-Control','no-store')
                self.end_headers();return
            page=Path(__file__).resolve().parent/'followups.html'
            if not page.is_file():return self.reply(404,{'error':'Экран ещё не опубликован'})
            body=page.read_bytes();self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','same-origin')
            self.end_headers();return self.wfile.write(body)
        if path=='/client-audit.js':
            body=(PUBLIC/'client-audit.js').read_bytes();self.send_response(200)
            self.send_header('Content-Type','text/javascript; charset=utf-8');self.send_header('Cache-Control','no-store')
            self.end_headers();return self.wfile.write(body)
        if path=='/api/client-audit':
            if not self.auth():return
            try:return self.reply(200,client_audit.snapshot())
            except ValueError:return self.reply(404,{'error':'Разбор ещё не загружен'})
        if path=='/api/campaign-preview':
            if not self.auth():return
            campaign=parse_qs(urlsplit(self.path).query).get('id',[''])[0]
            with bridge.db() as d: rows=[dict(r) for r in d.execute('SELECT name,phone,body,state FROM messages WHERE campaign=? ORDER BY id',(campaign,))]
            return self.reply(200,{'preview':rows})
        if path!='/api/status': return self.reply(404,{'error':'Not found'})
        if not self.auth(): return
        try:
            with bridge.db() as d:
                messages=[dict(r) for r in d.execute('SELECT * FROM messages ORDER BY id DESC LIMIT 500')]
                campaigns=[dict(r) for r in d.execute('SELECT * FROM campaigns ORDER BY start DESC')]
                day=bridge.now().date().isoformat()
                accepted=d.execute("SELECT count(*) FROM messages WHERE state='accepted' AND substr(attempted,1,10)=?",(day,)).fetchone()[0]
                pending=d.execute("SELECT count(*) FROM messages WHERE state='pending'").fetchone()[0]
            with bridge.db() as d:
                cohort_counts={r['audience']:r['n'] for r in d.execute('SELECT c.audience,count(*) AS n FROM messages m JOIN campaigns c ON c.id=m.campaign WHERE substr(m.attempted,1,10)=? GROUP BY c.audience',(day,))}
            config=bridge.config()
            return self.reply(200,{'date':day,'timezone':'Asia/Dubai','accepted_today':accepted,'pending':pending,'limit':int(config.get('daily_limit',45)), 'messages':messages,'campaigns':campaigns,'worker_enabled':os.environ.get('WORKER_ENABLED')=='1','unread':None,'replies':None,'audiences':{'existing':{'limit':30,'attempted_today':cohort_counts.get('existing',0)},'new':{'limit':15,'attempted_today':cohort_counts.get('new',0)}}})
        except Exception: return self.reply(503,{'error':'Хранилище или интеграция не настроены'})
    def do_POST(self):
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=1_000_000: return self.reply(413,{'error':'Invalid request size'})
            data=json.loads(self.rfile.read(size));path=urlsplit(self.path).path
            origin=self.headers.get('Origin')
            if origin and origin not in (ORIGIN,'https://n8n.alekperovs.com'):return self.reply(403,{'error':'Origin rejected'})
            if path in ('/api/admin/setup','/api/admin/login'):
                try:
                    if path.endswith('/setup'):token=admin.setup(data.get('code',''),data.get('email',''),data.get('password',''))
                    else:token=admin.login(data.get('email',''),data.get('password',''))
                    return self.reply(200,{'ok':True},self.cookie(token))
                except ValueError as e:return self.reply(400,{'error':str(e)})
            origin=self.headers.get('Origin')
            # Browser mutations must originate from this dashboard or its direct VPS host.
            if origin and origin not in (ORIGIN,'https://n8n.alekperovs.com'):
                return self.reply(403,{'error':'Origin rejected'})
            if not self.auth():return
            if path=='/api/admin/logout':
                admin.logout(self.session());return self.reply(200,{'ok':True},self.cookie('',0))
            with (bridge.ROOT/'worker.lock').open('a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX)
                if path=='/api/client-audit-review':return self.reply(200,client_audit.review(data))
                if path=='/api/client-audit-draft':return self.reply(201,client_audit.draft(data))
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
                        args=types.SimpleNamespace(id=data['id'],file=str(p/'contacts.csv'),text=str(p/'text.txt'),daily=int(data.get('daily',30)),days=int(data.get('days',7)),start=data.get('start'),audience=data.get('audience','existing'))
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

SETUP_PAGE = """<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Property Connect — Администратор</title><style>body{background:#101112;color:#eeece6;font:16px system-ui;margin:0;display:grid;min-height:100vh;place-items:center}main{max-width:430px;padding:35px;background:#191b1d;border:1px solid #343638;border-radius:12px}h1{font-size:25px}p{color:#999;line-height:1.6}input,button{font:inherit;box-sizing:border-box;width:100%;padding:13px;border-radius:7px;margin:8px 0 20px}input{background:#111;color:#eee;border:1px solid #444}button{background:#c6ad80;color:#111;border:0;cursor:pointer}</style><main><p>PROPERTY / CONNECT</p><h1>Создать администратора</h1><p>Задайте email и пароль для своей админ-панели. Эта страница работает один раз.</p><form id="form"><label>Email<input id="email" type="email" required autocomplete="username"></label><label>Пароль<input id="password" type="password" minlength="10" maxlength="200" required autocomplete="new-password"></label><label>Повторите пароль<input id="repeat" type="password" minlength="10" required autocomplete="new-password"></label><button>Создать аккаунт</button></form><p id="status"></p></main><script>const code=__SETUP_TOKEN__;const base=location.pathname.split('/setup/')[0];document.querySelector('#form').onsubmit=async e=>{e.preventDefault();const p=document.querySelector('#password').value;if(p!==document.querySelector('#repeat').value){document.querySelector('#status').textContent='Пароли не совпадают';return}const r=await fetch(base+'/api/admin/setup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code,email:document.querySelector('#email').value,password:p})});const d=await r.json();if(!r.ok){document.querySelector('#status').textContent=d.error;return}location.href=base+'/'};</script></html>"""

if __name__=='__main__':
    os.umask(0o077)
    ThreadingHTTPServer(('0.0.0.0',int(os.environ.get('PORT',8080))),Handler).serve_forever()
