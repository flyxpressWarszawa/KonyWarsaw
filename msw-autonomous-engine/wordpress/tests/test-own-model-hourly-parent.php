<?php
declare(strict_types=1);

/** Isolated one-process regression: no WordPress, network, real DB, or checkpoint bytes. */
namespace MSW\UnifiedEngine {
    final class RemoteStore {
        public static array $jobs=[];
        public static array $rows=[];
        public static function ready():bool {return true;}
        public static function config():array {
            return ['enabled'=>true,'nodes'=>['msw-render-central-1'=>['kinds'=>['llm.generate']]]];
        }
        public static function table():string {return 'msw_fake_jobs';}
        public static function enqueue(string $kind,array $payload,string $key=''):array {
            if($kind!=='llm.generate'||$key==='')throw new \RuntimeException('unexpected_job');
            $hash=hash('sha256',json_encode($payload,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES));
            if(isset(self::$jobs[$key])&&self::$jobs[$key]['payload_sha256']!==$hash)throw new \RuntimeException('idempotency_conflict');
            self::$jobs[$key]=['kind'=>$kind,'payload'=>$payload,'payload_sha256'=>$hash];
            return ['job_id'=>$key,'payload_sha256'=>$hash];
        }
    }
}
namespace {
    define('ABSPATH', __DIR__.'/');
    define('ARRAY_A','ARRAY_A');

    class WP_Error extends \Exception {}
    class MSW_Business_Director {
        public static function central():bool {return true;}
        public static function allowed():bool {return true;}
        public static function settings():array {return ['enabled'=>true,'owner_id'=>1];}
    }
    class MSW_Central_Engine_Core {
        public static function settings():array {return ['enabled'=>true,'autonomy_enabled'=>true];}
    }
    function add_action(...$args):void {}
    function user_can($id,$cap):bool {return $id===1&&$cap==='manage_options';}
    function is_wp_error($value):bool {return $value instanceof WP_Error;}
    function get_option(string $key,$default=[]){return $GLOBALS['msw_test_options'][$key]??$default;}
    function update_option(string $key,$value,$autoload=false):bool {
        $GLOBALS['msw_test_options'][$key]=$value;
        return true;
    }
    final class MSWTestDB {
        public function prepare(string $sql,...$values):string {return (string)end($values);}
        public function get_row(string $id,$format) {
            return \MSW\UnifiedEngine\RemoteStore::$rows[$id]??null;
        }
    }
    $GLOBALS['wpdb']=new MSWTestDB();

    $target=dirname(__DIR__).'/msw-central-engine/includes/class-msw-ce-own-model.php';
    $manifest=dirname($target,2).'/assets/own-model-manifest.php';
    if(is_file($manifest))throw new \RuntimeException('unexpected_preexisting_manifest');
    if(!is_dir(dirname($manifest))&&!mkdir(dirname($manifest),0700,true))throw new \RuntimeException('fixture_dir');
    file_put_contents($manifest, "<?php\nreturn ".var_export(['parts'=>[['sha256'=>str_repeat('a',64),'bytes'=>17]]],true).";\n");
    register_shutdown_function(static function()use($manifest):void {
        @unlink($manifest);
        @rmdir(dirname($manifest));
    });
    require_once $target;

    function require_true(bool $pass,string $reason):void {
        if(!$pass)throw new \RuntimeException('TEST FAILURE: '.$reason);
    }
    function reply(string $id,string $payloadHash,array $data):void {
        $wire=json_encode(['ok'=>true,'data'=>['provider_ref'=>'MSW-DualMemory-byte-v1','research_only'=>true]+$data],
                          JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES);
        \MSW\UnifiedEngine\RemoteStore::$rows[$id]=[
            'state'=>'completed','kind'=>'llm.generate','payload_sha256'=>$payloadHash,
            'result'=>$wire,'result_sha256'=>hash('sha256',$wire),
        ];
    }
    function fixture_state():void {
        \MSW\UnifiedEngine\RemoteStore::$rows=[];
        \MSW\UnifiedEngine\RemoteStore::$jobs=[];
        $GLOBALS['msw_test_options']=[
            'mswue_remote_seen_v1'=>['msw-render-central-1'=>['capabilities'=>['llm.generate']]],
            'msw_bdir_own_model_v24'=>[
                'parts'=>[['id'=>'part-0','payload_sha256'=>'P0']],
                'status'=>['id'=>'historical-status','payload_sha256'=>'S0'],
                'training'=>['id'=>'last-rejected-training','payload_sha256'=>'T0'],
                'training_slot'=>'yesterday',
            ],
        ];
        reply('part-0','P0',['action'=>'checkpoint_part','staged'=>true,'checkpoint_ready'=>true]);
        reply('historical-status','S0',[
            'action'=>'status','checkpoint_sha256'=>str_repeat('a',64),
            'training_performed'=>false,
        ]);
        reply('last-rejected-training','T0',[
            'action'=>'train','training_performed'=>true,'promoted_to_research_current'=>false,
            'checkpoint_sha256'=>str_repeat('c',64),
        ]);
    }

    // Regress the historical bug: old snapshot SHA-A and rejected candidate SHA-C
    // must not overwrite the actual current SHA-B.
    fixture_state();
    $first=MSW_BDir_Own_Model_20261105::seed();
    require_true($first['state']==='fresh_checkpoint_status_queued','first call must request signed fresh status');
    $s=get_option('msw_bdir_own_model_v24');
    $fresh=$s['fresh_status'];
    require_true(!isset($s['training_slot'])||$s['training_slot']==='yesterday','no training before fresh result');
    $statusJob=\MSW\UnifiedEngine\RemoteStore::$jobs[$fresh['id']];
    require_true($statusJob['payload']['action']==='status','fresh job is status only');
    require_true(($statusJob['payload']['parent_sha256']??'')==='','never pin stale parent for status');
    reply($fresh['id'],$fresh['payload_sha256'],[
        'action'=>'status','training_performed'=>false,'checkpoint_sha256'=>str_repeat('b',64),
    ]);
    $second=MSW_BDir_Own_Model_20261105::seed();
    require_true($second['state']==='existing_python_queue','second call can enqueue');
    $s=get_option('msw_bdir_own_model_v24');
    $job=\MSW\UnifiedEngine\RemoteStore::$jobs[$s['training']['id']];
    require_true($job['payload']['action']==='train','existing bounded training action');
    require_true($job['payload']['parent_sha256']===str_repeat('b',64),'fresh current checkpoint required');
    require_true($job['payload']['parent_sha256']!==str_repeat('a',64),'must reject historical snapshot');
    $before=count(\MSW\UnifiedEngine\RemoteStore::$jobs);
    MSW_BDir_Own_Model_20261105::seed();
    require_true(count(\MSW\UnifiedEngine\RemoteStore::$jobs)===$before,'no duplicate hourly job');

    // A pending/uncertain result must never be silently replaced.
    fixture_state();
    unset(\MSW\UnifiedEngine\RemoteStore::$rows['last-rejected-training']);
    $pending=MSW_BDir_Own_Model_20261105::seed();
    require_true($pending['state']==='waiting_for_previous_training_receipt','hold uncertain previous job');
    require_true(count(\MSW\UnifiedEngine\RemoteStore::$jobs)===0,'no status or train while uncertain');

    // A malformed fresh receipt must never authorize a training job.
    fixture_state();
    MSW_BDir_Own_Model_20261105::seed();
    $s=get_option('msw_bdir_own_model_v24');
    $fresh=$s['fresh_status'];
    reply($fresh['id'],$fresh['payload_sha256'],[
        'action'=>'status','training_performed'=>false,'checkpoint_sha256'=>'not-a-checkpoint',
    ]);
    $invalid=MSW_BDir_Own_Model_20261105::seed();
    require_true($invalid['state']==='fresh_checkpoint_status_invalid','invalid pinned current checkpoint rejected');
    require_true(count(\MSW\UnifiedEngine\RemoteStore::$jobs)===1,'no training from malformed status');

    echo "PASS 4 isolated scheduler regression scenarios: current parent, no duplicate, uncertain HOLD, invalid status.\n";
}
