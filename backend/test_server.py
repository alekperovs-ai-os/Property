import sys,unittest,tempfile,threading,json,urllib.request,urllib.error,os
from pathlib import Path
from unittest.mock import patch
from datetime import datetime
sys.path.insert(0,str(Path(__file__).resolve().parent))
import bridge,server,admin
class ServerTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.old=bridge.ROOT;bridge.ROOT=Path(self.temp.name)
  self.key='a'*40;os.environ['ADMIN_SETUP_TOKEN']=self.key
  (bridge.ROOT/'secrets.json').write_text(json.dumps({'api_url':'https://api.green-api.com','instance':'123','token':'private-test-key'}))
  self.session=admin.setup(self.key,'owner@example.com','Test-password-123456')
  self.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
  self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
 def tearDown(self):
  self.http.shutdown();self.http.server_close();self.thread.join();bridge.ROOT=self.old;self.temp.cleanup()
 def request(self,path,data=None,key=None):
  req=urllib.request.Request('http://127.0.0.1:'+str(self.http.server_port)+path,data=json.dumps(data).encode() if data is not None else None,headers={'Cookie':'property_session='+(key or self.session),'Content-Type':'application/json'})
  try:
   with urllib.request.urlopen(req) as r:return r.status,json.load(r)
  except urllib.error.HTTPError as e:return e.code,json.load(e)
 def draft(self):
  return self.request('/api/campaigns',{'id':'test','text':'Hello {name}','contacts':[{'phone':'+971501234567','name':'Anna'}],'daily':1,'days':1})
 def test_email_password_login_and_logout(self):
  code,data=self.request('/api/admin/login',{'email':'owner@example.com','password':'wrong'});self.assertEqual(code,400)
  self.assertEqual(self.request('/api/admin/login',{'email':'owner@example.com','password':'Test-password-123456'})[0],200)
  self.assertEqual(self.request('/api/admin/setup',{'code':self.key,'email':'attacker@example.com','password':'DifferentPassword123'})[0],400)
  self.assertEqual(self.request('/api/admin/logout',{})[0],200)
  self.assertEqual(self.request('/api/status')[0],401)
 def test_auth_and_no_secret_exposure(self):
  self.assertEqual(self.request('/api/status',key='wrong')[0],401)
  code,data=self.request('/api/status');self.assertEqual(code,200)
  self.assertNotIn('private-test-key',json.dumps(data))
 def test_preview_activation_confirmation_and_pause(self):
  code,data=self.draft();self.assertEqual(code,201);self.assertEqual(data['preview'][0]['body'],'Hello Anna')
  self.assertEqual(self.request('/api/campaign-state',{'id':'test','state':'active'})[0],400)
  self.assertEqual(self.request('/api/campaign-state',{'id':'test','state':'active','confirm':True})[0],200)
  self.assertEqual(self.request('/api/pause-all',{})[0],200)
  self.assertEqual(self.request('/api/status')[1]['campaigns'][0]['state'],'paused')
 def test_uncertain_blocks_activation_and_recovers_after_crash(self):
  self.draft()
  with bridge.db() as d:
   d.execute("UPDATE campaigns SET state='active'")
   d.execute("INSERT INTO messages(campaign,phone,name,body,state) VALUES('test','971501234568','Ivan','Hello','pending')")
   d.execute("UPDATE messages SET state='uncertain' WHERE phone='971501234567'")
  self.assertEqual(self.request('/api/campaign-state',{'id':'test','state':'active','confirm':True})[0],400)
  with patch.object(bridge,'now',return_value=datetime(2026,10,9,12,tzinfo=bridge.TZ)),patch.object(bridge,'api') as api:
   self.assertEqual(bridge.tick()['state'],'uncertain_campaign_paused');api.assert_not_called()
 def test_private_followups_require_session(self):
  page=Path(server.__file__).resolve().parent/'followups.html'
  marker='Клиенты и следующий шаг'
  base='http://127.0.0.1:'+str(self.http.server_port)
  with urllib.request.urlopen(base+'/followups.html') as r:
   self.assertNotIn('data-full="true"',r.read().decode())
   self.assertTrue(r.url.endswith('index.html?next=followups'))
  req=urllib.request.Request(base+'/followups.html',headers={'Cookie':'property_session='+self.session})
  with urllib.request.urlopen(req) as r:
   self.assertEqual(r.status,200)
   self.assertEqual(r.headers['Cache-Control'],'no-store')
   self.assertIn('data-full="true"',r.read().decode())
 def test_private_audit_and_review_draft(self):
  record={'id':'971501234567@c.us','phone':'971501234567','name':'Anna','category':'candidate','text':'Personal message'}
  (bridge.ROOT/'client-audit-summary.json').write_text(json.dumps({'records':[record]}))
  self.assertEqual(self.request('/api/client-audit',key='wrong')[0],401)
  self.assertEqual(self.request('/api/client-audit-review',{'id':record['id'],'decision':'include','text':'Personal message'})[0],200)
  self.assertEqual(self.request('/api/client-audit-draft',{})[0],400)
  code,result=self.request('/api/client-audit-draft',{'checked_replies':True})
  self.assertEqual(code,201);self.assertEqual(result['state'],'draft')
  self.assertEqual(self.request('/api/status')[1]['messages'][0]['body'],'Personal message')
  self.assertEqual(self.request('/api/client-audit-draft',{'checked_replies':True})[0],400)
 def test_audit_excluded_cannot_be_selected(self):
  (bridge.ROOT/'client-audit-summary.json').write_text(json.dumps({'records':[{'id':'excluded','category':'excluded'}]}))
  self.assertEqual(self.request('/api/client-audit-review',{'id':'excluded','decision':'include','text':'Hello'})[0],400)
 def test_cross_campaign_duplicate_rejected(self):
  self.draft()
  self.assertEqual(self.request('/api/campaigns',{'id':'another','text':'hello  Anna','contacts':[{'phone':'+971501234567','name':'Anna'}],'daily':1,'days':1})[0],400)
 def test_audit_opt_out_blocks_new_campaign(self):
  (bridge.ROOT/'client-audit-summary.json').write_text(json.dumps({'records':[{'phone':'971501234567','category':'excluded'}]}))
  self.assertEqual(self.draft()[0],400)
 def test_duplicate_campaign_does_not_duplicate_messages(self):
  self.draft();self.assertEqual(self.draft()[0],400)
  self.assertEqual(len(self.request('/api/status')[1]['messages']),1)
if __name__=='__main__': unittest.main()
