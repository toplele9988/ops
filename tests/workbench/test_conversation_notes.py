import tempfile,unittest,sqlite3,threading
from pathlib import Path
from console import conversation_notes as n

class NotesTest(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name).resolve()/'notes.sqlite3';n.initialize(self.path);self.scope=('a'*64,'alibaba','account_a','scope-one','conversation-1')
 def tearDown(self):self.tmp.cleanup()
 def save(self,actor='admin',status='following',content='LED details',expected=0,scope=None):
  return n.save(*(scope or self.scope),actor=actor,status=status,content=content,expected_version=expected,path=self.path,clock=lambda:123)
 def read(self,scope=None):return n.read(*(scope or self.scope),path=self.path)
 def test_initial_save_reload_and_two_equal_users(self):
  self.assertEqual(self.read()['version'],0);self.assertEqual(self.save(),1)
  self.assertEqual(self.read()['content'],'LED details');self.assertEqual(self.save(actor='staff',content='next',expected=1),2)
  self.assertEqual(self.read()['updated_by'],'staff');self.assertEqual(self.read()['version'],2)
 def test_exact_scope_prevents_cross_account_channel_and_conversation(self):
  self.save()
  for vals in [('a'*64,'alibaba','account_b','scope-one','conversation-1'),('a'*64,'wechat','account_a','scope-one','conversation-1'),('a'*64,'alibaba','account_a','scope-two','conversation-1'),('a'*64,'alibaba','account_a','scope-one','conversation-2')]:
   with self.assertRaises(n.ScopeMismatch):self.read(vals)
  other=('b'*64,'alibaba','account_a','scope-one','conversation-2');self.assertFalse(self.read(other)['exists'])
 def test_same_time_saves_one_wins_without_overwrite(self):
  self.save();barrier=threading.Barrier(2);results=[]
  def run(actor):
   barrier.wait()
   try:results.append(('ok',self.save(actor=actor,content=actor,expected=1)))
   except n.Conflict:results.append(('conflict',actor))
  threads=[threading.Thread(target=run,args=(a,)) for a in ('admin','staff')]
  for t in threads:t.start()
  for t in threads:t.join()
  self.assertEqual(sorted(x[0] for x in results),['conflict','ok']);self.assertEqual(self.read()['version'],2)
  with sqlite3.connect(self.path) as c:self.assertEqual(c.execute('select count(*) from note_events').fetchone()[0],2)
 def test_invalid_input_cannot_create_or_mutate(self):
  for kw in ({'content':'x'*513},{'status':'fake'},{'actor':'x\n'},{'expected':-1}):
   with self.assertRaises(ValueError):self.save(**kw)
  self.assertFalse(self.read()['exists'])
 def test_audit_append_only(self):
  self.save()
  with sqlite3.connect(self.path) as c:
   with self.assertRaises(sqlite3.IntegrityError):c.execute('delete from note_events')
 def test_missing_database_does_not_create(self):
  self.path.unlink()
  with self.assertRaises(FileNotFoundError):self.read()
  self.assertFalse(self.path.exists())
 def test_unrelated_existing_database_never_reinitialized(self):
  with sqlite3.connect(self.path) as c:c.execute('pragma application_id=0')
  with self.assertRaises(ValueError):n.initialize(self.path)

 def test_modified_schema_is_rejected_without_erasing_data(self):
  self.save()
  with sqlite3.connect(self.path) as c:c.execute('drop trigger note_events_no_delete')
  with self.assertRaises(ValueError):self.read()
  with self.assertRaises(ValueError):n.initialize(self.path)
  with sqlite3.connect(self.path) as c:self.assertEqual(c.execute('select count(*) from conversation_notes').fetchone()[0],1)
