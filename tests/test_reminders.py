import datetime
import unittest
import test_domain
import domain as d


class ReminderTests(unittest.TestCase):
 setUp=test_domain.DomainTests.setUp
 tearDown=test_domain.DomainTests.tearDown
 call=test_domain.DomainTests.call
 snap=test_domain.DomainTests.snap

 def appointment(self,offset=2):
  self.call(self.mom,'pregnancy.create',{'consent':True})
  aid=self.call(self.mom,'appointment.create',{'title':'合成预约','date':str(d.today()+datetime.timedelta(days=offset))})['id']
  return aid
 def read(self,kind,id):
  with d.transaction() as c:return d.get(c,kind,id)
 def notices(self,ref):
  with d.transaction() as c:return [n for n in d.rows(c,'notification',self.fid) if n.get('ref')==ref]
 def update_appointment(self,id,**data):
  a=self.read('appointment',id);self.call(self.mom,'appointment.update',dict(data,id=id,version=a['version']))

 def test_appointment_reschedule_cancels_old_and_reminds_new_date_once(self):
  aid=self.appointment();d.tick();d.tick();self.assertEqual(len(self.notices(aid)),1)
  self.update_appointment(aid,date=str(d.today()+datetime.timedelta(days=10)),title='调整后的安排',note='新材料')
  self.assertTrue(self.notices(aid)[0]['cancelled']);d.tick();self.assertEqual(len(self.notices(aid)),1)
  self.update_appointment(aid,date=str(d.today()+datetime.timedelta(days=1)))
  d.tick();d.tick();ns=self.notices(aid)
  self.assertEqual(len(ns),2);self.assertEqual(sum(not n.get('cancelled') for n in ns),1)
  self.assertEqual(self.read('appointment',aid)['note'],'新材料')

 def test_completed_and_cancelled_appointment_reminders_are_cancelled(self):
  aid=self.appointment();d.tick()
  self.update_appointment(aid,status='done');self.assertTrue(self.notices(aid)[0]['cancelled'])
  self.update_appointment(aid,status='scheduled');d.tick();self.assertEqual(len(self.notices(aid)),2)
  self.update_appointment(aid,status='cancelled');d.tick()
  self.assertTrue(all(n.get('cancelled') for n in self.notices(aid)))

 def test_old_appointment_and_task_do_not_create_backlog(self):
  aid=self.appointment(-1)
  tid=self.call(self.dad,'task.create',{'title':'过期待办','due_date':str(d.today()-datetime.timedelta(days=1))})['id']
  d.tick();self.assertEqual(self.notices(aid),[]);self.assertEqual(self.notices(tid),[])

 def test_task_completion_and_reassignment_cancel_previous_reminder(self):
  tid=self.call(self.dad,'task.create',{'title':'今天的安排','due_date':str(d.today())})['id'];d.tick()
  t=self.read('task',tid);self.call(self.dad,'task.update',{'id':tid,'version':t['version'],'status':'done'})
  self.assertTrue(all(n.get('cancelled') for n in self.notices(tid)))
  t=self.read('task',tid);self.call(self.dad,'task.update',{'id':tid,'version':t['version'],'status':'todo'})
  t=self.read('task',tid);self.call(self.dad,'task.update',{'id':tid,'version':t['version'],'assignee':self.mom})
  d.tick();ns=self.notices(tid)
  self.assertEqual(sum(not n.get('cancelled') for n in ns),1)
  self.assertEqual(next(n['recipient'] for n in ns if not n.get('cancelled')),self.mom)

 def test_task_preference_off_cancels_existing_unread_reminders(self):
  tid=self.call(self.dad,'task.create',{'title':'今天的安排','due_date':str(d.today())})['id'];d.tick()
  self.call(self.dad,'notification.preferences',{'enabled':False});d.tick()
  self.assertTrue(self.notices(tid)[0]['cancelled']);self.assertEqual(len(self.notices(tid)),1)

 def test_other_family_member_cannot_edit_appointment(self):
  aid=self.appointment();a=self.read('appointment',aid)
  with self.assertRaises(d.Problem) as err:self.call(self.dad,'appointment.update',{'id':aid,'version':a['version'],'date':str(d.today())})
  self.assertEqual(err.exception.status,403)
  self.assertEqual(self.read('appointment',aid)['date'],a['date'])

 def test_pause_resume_does_not_replay_cancelled_notice(self):
  aid=self.appointment();d.tick();p=self.snap(self.mom)['pregnancy']
  self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'pause'})
  self.assertTrue(self.notices(aid)[0]['cancelled']);p=self.snap(self.mom)['pregnancy']
  self.call(self.mom,'pregnancy.event',{'id':p['id'],'version':p['version'],'event':'resume'})
  d.tick();self.assertEqual(len(self.notices(aid)),1)
