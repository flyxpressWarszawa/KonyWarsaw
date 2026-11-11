"""Offline source-scoped tests for the MSW module-bus singleflight guard.

PHP here is synthetic and NEVER runs WordPress, a remote provider or a live
queue. The test literally replaces the two original production source lines.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tools.stage_module_bus_singleflight import (
    MARKER, ORIGINAL, digest, make_candidate, restore_staged, stage_source,
)

PREFIX = r'''<?php
namespace MSW\UnifiedEngine {
    final class RemoteStore {
        public static int $created=0;
        public static function table():string{return 'ovse_mswue_remote_jobs';}
        public static function enqueue(string $kind,array $payload,string $key):array{
            self::$created++;
            return ['job_id'=>'11111111-2222-3333-4444-555555555555','state'=>'pending'];
        }
    }
}
namespace {
    const ARRAY_A='ARRAY_A';
    function user_can($id,$cap):bool{return true;}
    function wp_json_encode($v,int $flags=0):string{return json_encode($v,$flags);}
    final class FakeDB {
        public ?array $row=null;
        public int $reads=0;
        public function prepare(string $sql,string $id):string{return $id;}
        public function get_row($sql,$mode):?array{$this->reads++;return $this->row;}
    }
    final class MSW_Federation_Module_Bus {
        private static function state():array{return $GLOBALS['state'];}
        private static function catalog():array{return $GLOBALS['catalog'];}
        private static function json($v):string{return (string)wp_json_encode($v,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES);}
        private static function error(string $name,int $status=403):array{return ['error'=>$name,'http'=>$status];}
        private static function mutate(callable $callback):bool {
            if (!empty($GLOBALS['fail_lock']))return false;
            $s=$GLOBALS['state'];
            if (!empty($GLOBALS['option_race']))$s['last_queued']['job_id']='bbbbbbbb-cccc-dddd-eeee-ffffffffffff';
            $callback($s);
            $GLOBALS['state']=$s;
            return true;
        }
        public static function enqueue() {
            $s=self::state();if(empty($s['enabled'])||!user_can((int)($s['owner_id']??0),'manage_options'))return self::error('not_connected',403);
            if(!class_exists('MSW\\UnifiedEngine\\RemoteStore'))return self::error('queue_missing',503);
'''

SUFFIX = r'''
        if(is_wp_error($job))return$job;
        $receipt=['job_id'=>$job['job_id'],'catalog_sha256'=>$all['catalog_sha256'],'total'=>$all['total'],'at'=>gmdate('c')];
        self::mutate(static function(&$s)use($receipt,$job){$s['last_checked']=time();$s['last_queued']=$receipt;
            if(($s['last_sync']['job_id']??null)!==$job['job_id'])$s['pending_receipts'][$job['job_id']]=['kind'=>'runtime.catalog'];});
        return $receipt+['queued'=>true,'completed'=>false];
        }
    }
    function is_wp_error($v):bool{return false;}
    $sha=str_repeat('a',64);
    $last=['job_id'=>'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee','catalog_sha256'=>$sha,'total'=>330,'at'=>'2026-11-11T21:42:48Z'];
    $GLOBALS['catalog']=['catalog_sha256'=>$sha,'total'=>330];
    $GLOBALS['state']=['enabled'=>true,'owner_id'=>1,'last_queued'=>$last,'last_checked'=>1,'pending_receipts'=>[]];
    $wpdb=new FakeDB;
    $expected=hash('sha256',wp_json_encode(['module_bus'=>['operation'=>'sync','catalog_sha256'=>$sha]],JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES));
    $wpdb->row=['state'=>'pending','kind'=>'runtime.catalog','payload_sha256'=>$expected];
    $scenario=$argv[1]??'pending';
    if($scenario==='running')$wpdb->row['state']='running';
    if($scenario==='failed')$wpdb->row['state']='failed';
    if($scenario==='digest_mismatch')$wpdb->row['payload_sha256']=str_repeat('0',64);
    if($scenario==='foreign_kind')$wpdb->row['kind']='llm.generate';
    if($scenario==='new_fingerprint')$GLOBALS['state']['last_queued']['catalog_sha256']=str_repeat('b',64);
    if($scenario==='lock_busy')$GLOBALS['fail_lock']=true;
    if($scenario==='option_race')$GLOBALS['option_race']=true;
    $result=MSW_Federation_Module_Bus::enqueue();
    echo json_encode(['result'=>$result,'new_jobs'=>\MSW\UnifiedEngine\RemoteStore::$created,
        'sql_reads'=>$wpdb->reads,'last_checked'=>$GLOBALS['state']['last_checked'],
        'pending_receipts'=>array_keys($GLOBALS['state']['pending_receipts'])]);
}
'''

FIXTURE = PREFIX + ORIGINAL + "\n" + SUFFIX


class MSWModuleBusSingleflightStageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "src" / "class-msw-module-bus.php"
        self.source.parent.mkdir()
        self.source.write_bytes(FIXTURE.encode())
        self.staging = self.root / "isolated"
        self.staging.mkdir(mode=0o700)
        self.source_sha = digest(self.source.read_bytes())

    def candidate(self):
        return make_candidate(self.source.read_bytes(), expected_sha256=self.source_sha)

    def test_single_exact_anchor_and_refuses_drift(self):
        assert self.candidate().count(MARKER.encode()) == 1
        with self.assertRaisesRegex(ValueError, "source_digest_changed"):
            make_candidate(self.source.read_bytes(), expected_sha256="0" * 64)
        with self.assertRaisesRegex(ValueError, "already_modified"):
            make_candidate(self.candidate(), expected_sha256=digest(self.candidate()))
        doubled = self.source.read_bytes().replace(ORIGINAL.encode(), (ORIGINAL + "\n" + ORIGINAL).encode())
        with self.assertRaisesRegex(ValueError, "source_anchor_not_unique"):
            make_candidate(doubled, expected_sha256=digest(doubled))

    def test_isolated_backup_readback_and_rollback(self):
        result = stage_source(self.source, self.staging, self.source_sha)
        self.assertEqual(result["state"], "STAGED_NOT_DEPLOYED")
        self.assertTrue(result["backup_readback_verified"])
        self.assertTrue(result["candidate_readback_verified"])
        self.assertEqual(digest(self.source.read_bytes()), self.source_sha)
        rollback = restore_staged(
            self.staging / "module-bus-candidate.php",
            self.staging / "module-bus-before.php",
            expected_candidate_sha256=result["candidate_sha256"],
        )
        self.assertEqual(rollback["state"], "STAGING_ROLLBACK_VERIFIED")
        self.assertEqual(digest((self.staging / "module-bus-candidate.php").read_bytes()), self.source_sha)
        self.assertEqual(digest(self.source.read_bytes()), self.source_sha)

    def test_no_overwrite_on_existing_or_unreadable_workspace(self):
        stage_source(self.source, self.staging, self.source_sha)
        with self.assertRaisesRegex(ValueError, "staging_path_already_exists"):
            stage_source(self.source, self.staging, self.source_sha)
        self.assertEqual(digest(self.source.read_bytes()), self.source_sha)

    def test_runtime_php_syntax_and_singleflight_matrix(self):
        self.assertIsNotNone(shutil.which("php"), "php CLI required for this CI gate")
        result = stage_source(self.source, self.staging, self.source_sha)
        candidate = self.staging / "module-bus-candidate.php"
        lint = subprocess.run(["php", "-l", str(candidate)], capture_output=True, text=True, timeout=20)
        self.assertEqual(lint.returncode, 0, lint.stderr)
        cases = {
            "pending": (0, True),
            "running": (0, True),
            "failed": (1, False),
            "digest_mismatch": (1, False),
            "foreign_kind": (1, False),
            "new_fingerprint": (1, False),
            "lock_busy": (0, False),
            "option_race": (0, False),
        }
        for scenario, (new_jobs, reused) in cases.items():
            with self.subTest(scenario=scenario):
                process = subprocess.run(["php", str(candidate), scenario], capture_output=True, text=True, timeout=20)
                self.assertEqual(process.returncode, 0, f"{scenario}: {process.stderr}")
                data = json.loads(process.stdout)
                self.assertEqual(data["new_jobs"], new_jobs)
                self.assertEqual(bool(data["result"].get("reused_pending")), reused)
                if reused:
                    self.assertTrue(data["result"]["queued"])
                    self.assertFalse(data["result"]["completed"])
                    self.assertGreater(data["last_checked"], 1)
                    self.assertEqual(len(data["pending_receipts"]), 1)
                if scenario in {"lock_busy", "option_race"}:
                    self.assertEqual(data["result"]["error"], "singleflight_state_uncertain")
                    self.assertFalse(data["result"].get("queued", False))
        self.assertEqual(result["production_changed"], False)

    def test_rollback_refuses_changed_candidate(self):
        result = stage_source(self.source, self.staging, self.source_sha)
        file = self.staging / "module-bus-candidate.php"
        file.write_text("unrelated")
        with self.assertRaisesRegex(ValueError, "candidate_changed"):
            restore_staged(file, self.staging / "module-bus-before.php",
                           expected_candidate_sha256=result["candidate_sha256"])
        self.assertEqual(digest(self.source.read_bytes()), self.source_sha)


if __name__ == "__main__":
    unittest.main()
