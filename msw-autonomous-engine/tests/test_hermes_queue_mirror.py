import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from msw_engine.hermes_queue_mirror import (
    SCHEMA, Store, Mirror, MirrorError, GitHubPrivateVault,
    archive_key, WordPressReader,
)

JOB_ID='12345678-1234-1234-1234-123456789abc'
NOW=1794569000


def record(**changes):
    payload='{"schema":"msw.native-autonomy.job.v1","execution_id":"'+('a'*64)+'"}'
    r={'id':JOB_ID, 'kind':'module.execute', 'payload':payload,
       'payload_sha256':hashlib.sha256(payload.encode()).hexdigest(),
       'job_key':'b'*64,'state':'pending','attempts':0,
       'created_at':NOW-500,'updated_at':NOW-500}
    r.update(changes)
    return r


def page(rows, *, finished, until=NOW):
    last=rows[-1] if rows else None
    return {'schema':SCHEMA,'state':'pending','snapshot_until':until,'records':rows,
            'next_cursor': {'created_at':last['created_at'],'id':last['id']} if last else {'created_at':0,'id':''},
            'finished':finished,'archive_only_no_execution':True}


class FakeReader:
    def __init__(self, response):
        self.response=response
        self.calls=[]
    def fetch(self, state, cursor):
        self.calls.append((state,cursor.copy()))
        return self.response


class FakeVault:
    def __init__(self, fail=False):
        self.calls=[]
        self.fail=fail
    def put_page(self, state, page, sealed):
        self.calls.append((state,page,sealed))
        if self.fail: raise MirrorError('fake_github_timeout')
        return {'state':'uploaded_verified'}


class TestMirror(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.store=Store(self.root,b'\x03'*32)

    def test_encrypts_payload_at_rest_and_checks_digest(self):
        row=record()
        enc=self.store.save_page('pending',page([row],finished=True),self.store.cursor('pending',NOW),NOW)
        self.assertNotIn(row['payload'],str(enc))
        with sqlite3.connect(self.store.path) as db:
            nonce,blob=db.execute('SELECT nonce,ciphertext FROM jobs').fetchone()
        self.assertNotIn(row['payload'].encode(),blob)
        self.assertEqual(self.store.aes.decrypt(nonce,blob,self.store.aad(row)),row['payload'].encode())
        self.assertEqual(self.store.path.stat().st_mode & 0o777,0o600)

    def test_payload_mismatch_rejected_before_store(self):
        row=record(payload='bad')
        with self.assertRaisesRegex(MirrorError,'payload_digest_mismatch'):
            self.store.save_page('pending',page([row],finished=True),self.store.cursor('pending',NOW),NOW)
        with sqlite3.connect(self.store.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],0)

    def test_duplicate_retry_produces_same_ciphertext(self):
        row=record()
        p=page([row],finished=True)
        cursor=self.store.cursor('pending',NOW)
        first=self.store.save_page('pending',p,cursor,NOW)
        second=self.store.save_page('pending',p,cursor,NOW)
        self.assertEqual(first,second)
        with sqlite3.connect(self.store.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],1)

    def test_changed_payload_for_same_job_is_conflict(self):
        row=record()
        cursor=self.store.cursor('pending',NOW)
        self.store.save_page('pending',page([row],finished=True),cursor,NOW)
        newpayload='{}'
        altered=record(payload=newpayload,payload_sha256=hashlib.sha256(newpayload.encode()).hexdigest())
        with self.assertRaisesRegex(MirrorError,'existing_conflict'):
            self.store.save_page('pending',page([altered],finished=True),cursor,NOW)

    def test_failed_remote_write_does_not_advance_cursor(self):
        row=record()
        p=page([row],finished=True)
        vault=FakeVault(fail=True)
        task=Mirror(self.store,FakeReader(p),vault,clock=lambda:NOW)
        with self.assertRaisesRegex(MirrorError,'fake_github_timeout'):
            task.step()
        self.assertEqual(self.store.cursor('pending',NOW)['cursor_created_at'],0)
        self.assertFalse(self.store.cursor('pending',NOW)['finished'])

    def test_verified_remote_write_advances_cursor(self):
        p=page([record()],finished=True)
        vault=FakeVault()
        task=Mirror(self.store,FakeReader(p),vault,clock=lambda:NOW)
        status=task.step()
        self.assertEqual(status['state'],'page_archived')
        self.assertEqual(status['private_github_readback'],'uploaded_verified')
        self.assertTrue(self.store.cursor('pending',NOW)['finished'])
        self.assertEqual(task.step()['state'],'snapshot_complete')
        self.assertEqual(len(vault.calls),1)

    def test_no_private_github_refuses_to_begin_export(self):
        reader=FakeReader(page([record()],finished=True))
        task=Mirror(self.store,reader,clock=lambda:NOW)
        with self.assertRaisesRegex(MirrorError,'private_github_not_configured'):
            task.step()
        self.assertEqual(reader.calls,[])
        self.assertEqual(self.store.cursor('pending',NOW)['cursor_created_at'],0)

    def test_wrong_cursor_fails_without_commit(self):
        p=page([record()],finished=True)
        p['next_cursor']={'created_at':0,'id':''}
        with self.assertRaisesRegex(MirrorError,'page_cursor'):
            Mirror(self.store,FakeReader(p),FakeVault(),clock=lambda:NOW).step()
        self.assertEqual(self.store.cursor('pending',NOW)['cursor_created_at'],0)

    def test_state_scope_and_snapshot_are_checked(self):
        p=page([record()],finished=True)
        p['snapshot_until']=NOW+1
        with self.assertRaisesRegex(MirrorError,'page_scope'):
            Mirror(self.store,FakeReader(p),FakeVault(),clock=lambda:NOW).step()

    def test_completed_snapshot_rolls_forward_after_hour_without_rescanning_old_backlog(self):
        first = page([record()], finished=True)
        Mirror(self.store,FakeReader(first),FakeVault(),clock=lambda:NOW).step()
        self.assertTrue(self.store.cursor('pending',NOW+3599)['finished'])
        next_cursor=self.store.cursor('pending',NOW+3601)
        self.assertFalse(next_cursor['finished'])
        self.assertEqual(next_cursor['cursor_created_at'],NOW-1)
        self.assertEqual(next_cursor['snapshot_until'],NOW+3601)
        later=record(id='12345678-1234-1234-1234-123456789abd', created_at=NOW+2500,updated_at=NOW+2500)
        next_page=page([later],finished=True,until=NOW+3601)
        second=Mirror(self.store,FakeReader(next_page),FakeVault(),clock=lambda:NOW+3601).step()
        self.assertEqual(second['records_archived'],1)
        self.assertEqual(self.store.summary()['counts']['pending'],2)

    def test_archive_key_validation(self):
        self.assertEqual(archive_key(base64.b64encode(b'Z'*32).decode()),b'Z'*32)
        with self.assertRaises(MirrorError): archive_key('not_base64')
        with self.assertRaises(MirrorError): archive_key(base64.b64encode(b'x'*16).decode())

    def test_refuse_unscoped_repo_and_unpaired_wp(self):
        with self.assertRaises(MirrorError): GitHubPrivateVault('bad-name','x'*40)
        with self.assertRaises(MirrorError): WordPressReader('https://evil.com','msw-render-central-1','x'*40)
        with self.assertRaises(MirrorError): WordPressReader('https://mobilny-serwis-warszawa.pl','other-node','x'*40)

    def test_github_private_gate_denies_public(self):
        vault=GitHubPrivateVault('flyxpressWarszawa/KonyWarsaw','x'*40)
        vault.api=lambda path,**kw: {'private':False,'permissions':{'push':True},'default_branch':'main'}
        with self.assertRaisesRegex(MirrorError,'not_private'):
            vault.require_private()

    def test_github_private_gate_accepts_private(self):
        vault=GitHubPrivateVault('flyxpressWarszawa/queue-private','x'*40)
        vault.api=lambda path,**kw: {'private':True,'archived':False,'permissions':{'push':True},'default_branch':'main'}
        vault.require_private()
        self.assertEqual(vault.default_branch,'main')

    def test_github_existing_blob_is_idempotent(self):
        p=page([record()],finished=True)
        sealed=self.store.save_page('pending',p,self.store.cursor('pending',NOW),NOW)
        vault=GitHubPrivateVault('flyxpressWarszawa/queue-private','x'*40)
        vault.verified_at=__import__('time').time()
        vault.default_branch='main'
        def api(path,**kw):
            if kw.get('method')=='PUT':
                self.fail('should not write existing blob')
            data={'schema':SCHEMA,'state':'pending','snapshot_until':NOW,'next_cursor':p['next_cursor'],'records':sealed,'encrypted':True,'execution_allowed':False}
            return {'sha':vault.git_blob_sha(json.dumps(data,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode())}
        vault.api=api
        self.assertEqual(vault.put_page('pending',p,sealed)['state'],'existing_verified')

if __name__=='__main__': unittest.main()
