from test_bridge import Tests,b
from unittest.mock import patch
from datetime import datetime
class AccountTests(Tests):
 def second(self):
  (b.ROOT/'second-secrets.json').write_text('{}')
  d=b.db()
  with d:
   d.execute("INSERT INTO campaigns(id,state,daily,start,end,audience,account) VALUES('second','active',15,'2026-10-09','2026-10-09','new','second')")
   d.execute("INSERT INTO messages(campaign,phone,name,body) VALUES('second','971509999998','Second','Second hello')")
 def test_secondary_routes_without_primary_pending(self):
  self.second()
  with patch.object(b,'config',return_value={'daily_limit':45}),patch.object(b,'now',return_value=datetime(2026,10,9,10,tzinfo=b.TZ)),patch.object(b,'api',side_effect=[{'stateInstance':'authorized'},{'idMessage':'second-provider'}]) as api:
   self.assertEqual(b.tick()['state'],'accepted');self.assertEqual(api.call_args.kwargs['account'],'second')
 def test_secondary_cap_cannot_exceed_fifteen(self):
  self.second()
  with b.db() as d:
   for i in range(15):d.execute("INSERT INTO messages(campaign,phone,name,body,state,attempted) VALUES('second',?,'Past','Past','accepted','2026-10-09T09:00:00+04:00')",(str(971500000000+i),))
  with patch.object(b,'config',return_value={'daily_limit':45}),patch.object(b,'now',return_value=datetime(2026,10,9,12,tzinfo=b.TZ)),patch.object(b,'api') as api:
   self.assertEqual(b.tick()['state'],'daily_limit');api.assert_not_called()
 def test_cross_account_same_day_dedup(self):
  self.activate();self.second()
  with b.db() as d:d.execute("UPDATE messages SET state='accepted',attempted='2026-10-09T09:00:00+04:00' WHERE campaign='test'");d.execute("UPDATE messages SET phone='971501234567' WHERE campaign='second'")
  with patch.object(b,'config',return_value={}),patch.object(b,'now',return_value=datetime(2026,10,9,12,tzinfo=b.TZ)),patch.object(b,'api') as api:self.assertEqual(b.tick()['state'],'idle');api.assert_not_called()
 def test_interval_is_per_number(self):
  self.activate();self.second()
  with b.db() as d:d.execute("UPDATE messages SET state='accepted',attempted='2026-10-09T09:59:00+04:00' WHERE campaign='test'")
  with patch.object(b,'config',return_value={}),patch.object(b,'now',return_value=datetime(2026,10,9,10,tzinfo=b.TZ)),patch.object(b,'api',side_effect=[{'stateInstance':'authorized'},{'idMessage':'second-provider'}]) as api:self.assertEqual(b.tick()['state'],'accepted');self.assertEqual(api.call_args.kwargs['account'],'second')
 def test_second_cannot_create_existing(self):
  self.args.account='second';self.args.audience='existing';(b.ROOT/'second-secrets.json').write_text('{}')
  with self.assertRaises(ValueError):b.create(self.args)
