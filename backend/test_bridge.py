import importlib.util, pathlib, tempfile, unittest, types, zipfile
from unittest.mock import patch
from datetime import datetime
p=pathlib.Path(__file__).resolve().parent/'bridge.py'
s=importlib.util.spec_from_file_location('bridge',p); b=importlib.util.module_from_spec(s); s.loader.exec_module(b)
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.old=b.ROOT; b.ROOT=pathlib.Path(self.tmp.name)
  (b.ROOT/'contacts.csv').write_text('phone,name\n+971501234567,Anna\n+971501234567,Anna\n+971501234568,Ivan\n')
  (b.ROOT/'text.txt').write_text('Hello {name}')
  self.args=types.SimpleNamespace(id='test',file=str(b.ROOT/'contacts.csv'),text=str(b.ROOT/'text.txt'),daily=1,days=7,start='2026-10-09')
 def tearDown(self): b.ROOT=self.old; self.tmp.cleanup()
 def activate(self):
  b.create(self.args); d=b.db()
  with d: d.execute("UPDATE campaigns SET state='active'")
 def test_import_and_duplicate_campaign(self):
  self.assertEqual(b.create(self.args)['contacts'],2)
  with self.assertRaises(Exception): b.create(self.args)
 def test_phone(self):
  self.assertEqual(b.phone('+971 (50) 123-4567'),'971501234567')
  for value in ['123','0501234567','9e12']:
   with self.assertRaises(ValueError): b.phone(value)
 def test_limit_and_no_repeat(self):
  self.activate()
  with patch.object(b,'config',return_value={}), patch.object(b,'now',return_value=datetime(2026,10,9,10,tzinfo=b.TZ)), patch.object(b,'api',side_effect=[{'stateInstance':'authorized'},{'idMessage':'abc'}]):
   self.assertEqual(b.tick()['state'],'accepted')
  with patch.object(b,'config',return_value={}), patch.object(b,'now',return_value=datetime(2026,10,9,12,tzinfo=b.TZ)):
   self.assertEqual(b.tick()['state'],'idle')
 def test_uncertain_pauses(self):
  self.activate()
  with patch.object(b,'config',return_value={}), patch.object(b,'now',return_value=datetime(2026,10,9,10,tzinfo=b.TZ)), patch.object(b,'api',side_effect=[{'stateInstance':'authorized'},RuntimeError('timeout')]):
   self.assertEqual(b.tick()['state'],'uncertain')
  self.assertEqual(b.db().execute('select state from campaigns').fetchone()[0],'paused')
 def test_global_limit(self):
  self.activate(); d=b.db()
  with d: d.execute("UPDATE messages SET attempted='2026-10-09T09:00:00+04:00',state='uncertain'")
  with patch.object(b,'config',return_value={'daily_limit':1}),patch.object(b,'now',return_value=datetime(2026,10,9,12,tzinfo=b.TZ)):
   self.assertEqual(b.tick()['state'],'daily_limit')
 def test_block_and_expiry(self):
  self.activate();d=b.db()
  with d: d.execute('insert into blocked select phone from messages')
  with patch.object(b,'config',return_value={}),patch.object(b,'now',return_value=datetime(2026,10,9,12,tzinfo=b.TZ)):
   self.assertEqual(b.tick()['state'],'idle')
  with patch.object(b,'config',return_value={}),patch.object(b,'now',return_value=datetime(2026,11,9,12,tzinfo=b.TZ)):
   self.assertEqual(b.tick()['state'],'idle')
 def assert_cohort_limit(self,group,cap):
  self.activate()
  with b.db() as d:
   d.execute("INSERT INTO campaigns(id,state,daily,start,end,audience) VALUES('history','paused',30,'2026-10-09','2026-10-10',?)",(group,))
   for i in range(cap):d.execute("INSERT INTO messages(campaign,phone,name,body,state,attempted) VALUES('history',?,'Used','Used','accepted','2026-10-09T09:00:00+04:00')",(str(971500000000+i),))
   d.execute("INSERT INTO campaigns(id,state,daily,start,end,audience) VALUES('new','active',15,'2026-10-09','2026-10-10','new')")
   d.execute("INSERT INTO messages(campaign,phone,name,body) VALUES('new','971509999999','New','New greeting')")
  with patch.object(b,'config',return_value={'daily_limit':45}),patch.object(b,'now',return_value=datetime(2026,10,9,12,tzinfo=b.TZ)),patch.object(b,'api',side_effect=[{'stateInstance':'authorized'},{'idMessage':'quota-test'}]) as api:
   self.assertEqual(b.tick()['state'],'accepted')
   expected='971509999999@c.us' if group=='existing' else '971501234567@c.us'
   self.assertEqual(api.call_args.args[1]['chatId'],expected)
 def test_existing_quota_shared_across_campaigns(self):self.assert_cohort_limit('existing',30)
 def test_new_quota_does_not_block_existing(self):self.assert_cohort_limit('new',15)
 def test_legacy_campaign_migrates_to_existing(self):
  import sqlite3
  with sqlite3.connect(b.ROOT/'queue.sqlite') as d:
   d.execute('CREATE TABLE campaigns(id TEXT PRIMARY KEY,state TEXT,daily INTEGER,start TEXT,end TEXT)')
   d.execute("INSERT INTO campaigns VALUES('legacy','paused',1,'2026-10-09','2026-10-09')")
  with b.db() as d:self.assertEqual(d.execute("SELECT audience FROM campaigns WHERE id='legacy'").fetchone()[0],'existing')
 def test_xlsx(self):
  p=b.ROOT/'test.xlsx'
  with zipfile.ZipFile(p,'w') as z:
   z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Contacts" r:id="rId1"/></sheets></workbook>')
   z.writestr('xl/_rels/workbook.xml.rels','<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
   z.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c r="A1" t="inlineStr"><is><t>phone</t></is></c></row><row><c r="A2"><v>971501234567</v></c></row></sheetData></worksheet>')
  self.assertEqual(list(b.rows(p)),[{'phone':'971501234567'}])
if __name__=='__main__': unittest.main()
