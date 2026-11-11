<?php
/** Offline contract tests. Synthetic WordPress/DB ports; no live site or model. */
declare(strict_types=1);
define('ABSPATH',__DIR__.'/unused-wordpress/');define('ARRAY_A','ARRAY_A');

function expect(bool $condition,string $message='assertion_failed'): void { if(!$condition)throw new RuntimeException($message); }
function expect_fault(string $reason,callable $callback): void
{
    try{$callback();}catch(MSW_GCB_Fault $e){expect($e->reason===$reason,'expected '.$reason.', got '.$e->reason);return;}
    throw new RuntimeException('Expected fault '.$reason);
}
final class WP_Error
{
    public function __construct(private string $code,private string $message='',private $data=[]){ }
    public function get_error_code(): string { return $this->code; }
    public function get_error_data(){ return $this->data; }
}
function is_wp_error($v): bool{return $v instanceof WP_Error;}
function add_action(...$args): void{$GLOBALS['hooks'][]=['action',$args[0]];}
function add_filter(...$args): void{$GLOBALS['hooks'][]=['filter',$args[0]];}
function wp_cache_delete(...$args): bool{return true;}
function get_current_user_id(): int{return $GLOBALS['current_user'];}
function current_user_can(string $cap): bool{return user_can(get_current_user_id(),$cap);}
function user_can(int $user,string $cap): bool{return $cap==='manage_options'&&in_array($user,$GLOBALS['allowed_users'],true);}
function is_multisite(): bool{return false;}
function get_site_option($name,$default=[]){return $default;}
function get_option($name,$default=false){
    $raw=$GLOBALS['wpdb']->values[$name]??null;if($raw===null)return $default;
    if(preg_match('/\A(?:a|s|b|i|d|O):/',$raw))return unserialize($raw,['allowed_classes'=>false]);return $raw;
}
function get_transient($name){return $GLOBALS['transients'][$name]??false;}
function set_transient($name,$value,$ttl): bool{$GLOBALS['transients'][$name]=$value;return true;}
function register_rest_route($ns,$path,$args): void{$GLOBALS['routes'][$path]=$args;}
function wp_safe_remote_get($url,$args){
    $GLOBALS['http_calls'][]=['url'=>$url,'args'=>$args];
    if(isset($GLOBALS['during_http'])){($GLOBALS['during_http'])();}
    return $GLOBALS['http_response'];
}
function wp_remote_retrieve_response_code($r): int{return $r['status'];}
function wp_remote_retrieve_body($r): string{return $r['body'];}

/** Simulates row uniqueness/CAS semantics; it is not a production DB test. */
final class FakeDB
{
    public string $options='wp_options';public string $last_error='';public array $values=[];public $before_query=null;
    public function prepare($query,...$args): string{return json_encode([$query,$args],JSON_THROW_ON_ERROR);}
    public function esc_like($v): string{return addcslashes($v,'_%\\');}
    public function get_var($prepared){[$q,$a]=json_decode($prepared,true);return $this->values[$a[0]]??null;}
    public function query($prepared): int
    {
        [$q,$a]=json_decode($prepared,true);if($this->before_query!==null){$cb=$this->before_query;$this->before_query=null;$cb($q,$a);}
        if(str_starts_with($q,'INSERT IGNORE')){
            [$name,$raw]=$a;if(isset($this->values[$name]))return 0;
            for($i=2;$i<count($a);$i+=2)if(($this->values[$a[$i]]??null)!==$a[$i+1])return 0;
            $this->values[$name]=$raw;return 1;
        }
        if(str_starts_with($q,'UPDATE')){
            $n=substr_count($q,'INNER JOIN');$names=array_slice($a,0,$n);[$after,$name,$before]=array_slice($a,$n,3);
            if(($this->values[$name]??null)!==$before)return 0;
            foreach($names as $i=>$guard)if(($this->values[$guard]??null)!==$a[$n+3+$i])return 0;
            if($before===$after)return 0;$this->values[$name]=$after;return 1;
        }
        throw new RuntimeException('Unexpected SQL port');
    }
    public function get_results($prepared,$format): array
    {
        [$q,$a]=json_decode($prepared,true);$rows=[];
        foreach($this->values as $name=>$value){
            if(!str_starts_with($name,MSW_GCB_Options::PREFIX))continue;
            if(str_contains($q,'option_value LIKE')&&!str_contains($value,'"state":"recorded"')&&!str_contains($value,'"state":"leased"'))continue;
            $rows[]=['option_name'=>$name,'option_value'=>$value];
        }
        if(str_contains($q,'DESC'))$rows=array_reverse($rows);
        return array_slice($rows,0,str_contains($q,'LIMIT 100')?100:(int)end($a));
    }
}
final class MSW_Central_Engine_Core
{
    public const OPTION='msw_central_engine_settings';
    public static function defaults(): array{return ['enabled'=>true,'owner_id'=>1,'write_capabilities'=>['hub.sync'],'provider_order'=>['existing-provider']];}
}
final class MSW_Central_Engine_Registry
{
    public static array $definitions=[];
    public static function register($id,$definition): bool{self::$definitions[$id]=$definition;return true;}
    public static function catalog(): array{return self::$definitions;}
    public static function execute($id,$input,$request_id){
        $d=self::$definitions[$id];if(($d['permission_callback'])($input)!==true)return new WP_Error('denied');
        $result=($d['callback'])($input,['request_id'=>$request_id,'source'=>'msw-central-engine']);if(is_wp_error($result))return $result;
        $proof=($d['verify'])($input,$result);if(is_wp_error($proof)||($proof['verified']??false)!==true)return new WP_Error('unverified');
        return ['capability'=>$id,'effect'=>$d['effect'],'result'=>$result,'proof'=>$proof];
    }
}
final class MSW_Central_Engine_Workflows
{
    public static function validate($type,$input){return $type==='adapter.run'&&isset(MSW_Central_Engine_Registry::$definitions[$input['capability']??''])&&is_array($input['arguments']??null)?true:new WP_Error('invalid');}
}
final class MSW_Central_Engine_Store
{
    public static array $jobs=[];public static array $keys=[];public static array $artifacts=[];
    public static function enqueue($type,$input,$id,$owner){
        $key=$owner.':'.$id;$hash=hash('sha256',MSW_GCB_Contract::canonical([$type,$input]));
        if(isset(self::$keys[$key])){
            $old=self::$jobs[self::$keys[$key]];if($old['request_hash']!==$hash)return new WP_Error('conflict','',['status'=>409]);return $old+['duplicate'=>true];
        }
        $n=count(self::$jobs)+1;self::$keys[$key]=$n;
        return self::$jobs[$n]=['id'=>$n,'owner_id'=>$owner,'type'=>$type,'input'=>$input,'request_hash'=>$hash,'state'=>'pending','created_at'=>time(),
            'lease_until'=>0,'lease_token'=>'','runs'=>0,'result'=>[]];
    }
    public static function get_job($id){return self::$jobs[$id]??null;}
    public static function live($job): bool{
        $v=self::get_job($job['id']);return $v&&$v['state']==='running'&&$v['lease_until']>=time()&&hash_equals($v['lease_token'],$job['lease_token']);
    }
    public static function artifact($id){
        $a=self::$artifacts[$id]??null;if(!$a)return null;$a['readback_verified']=hash_equals($a['sha256'],hash('sha256',$a['data']));$a['data']=json_decode($a['data'],true);return $a;
    }
}

require dirname(__DIR__,2).'/wordpress/msw-github-coding-bridge/msw-github-coding-bridge.php';

function reset_ports(): void
{
    $GLOBALS['wpdb']=new FakeDB();$GLOBALS['current_user']=1;$GLOBALS['allowed_users']=[1,2];$GLOBALS['transients']=[];$GLOBALS['http_calls']=[];
    $GLOBALS['http_response']=['status'=>200,'body'=>''];unset($GLOBALS['during_http']);
    $GLOBALS['wpdb']->values[MSW_Central_Engine_Core::OPTION]=serialize(MSW_Central_Engine_Core::defaults()+['private_existing_setting'=>'preserve-this']);
    $GLOBALS['wpdb']->values['active_plugins']=serialize(['msw-ai-hub/msw-ai-hub.php','other/plugin.php','msw-invalid folder/a.php']);
    MSW_Central_Engine_Store::$jobs=[];MSW_Central_Engine_Store::$keys=[];MSW_Central_Engine_Store::$artifacts=[];
    MSW_Central_Engine_Registry::$definitions=[];MSW_GCB_Bridge::register();
}
function input_fixture(string $id='offline-test-request-01'): array
{
    return ['request_id'=>$id,'source_engine'=>'chatgpt','target'=>'technical-core','title'=>'Publiczny test kodowania','objective'=>'Dodaj odczytowy manifest. Wyłącznie dane testowe.',
        'acceptance'=>['Testy przechodzą.'],'public_data'=>true];
}
function enable_transport(): void{MSW_GCB_Bridge::connect(['transport_enabled'=>true]);}
function auth_fixture(string $id='9001'): array{return ['run_id'=>$id,'run_attempt'=>'1','workflow_sha'=>str_repeat('a',40),'expires_at'=>time()+500];}
function start_native(array $input): array
{
    $r=MSW_GCB_Bridge::submit($input);$id=$r['central_job_id'];
    MSW_Central_Engine_Store::$jobs[$id]['state']='running';MSW_Central_Engine_Store::$jobs[$id]['lease_until']=time()+180;
    MSW_Central_Engine_Store::$jobs[$id]['lease_token']=str_repeat('b',64);return $r;
}
function complete_native(array $admission,array $input): array
{
    $id=$admission['central_job_id'];$envelope=MSW_Central_Engine_Registry::execute('coding.submit',$input,'msw-ce-'.$id);
    expect(!is_wp_error($envelope),'native callback must pass');
    $aid='ce-'.$id.'-'.substr(hash('sha256','adapter'),0,16);
    $raw=json_encode(['capability'=>'coding.submit','result'=>$envelope,'checked_at'=>gmdate('c')],JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_THROW_ON_ERROR);
    $sha=hash('sha256',$raw);MSW_Central_Engine_Store::$artifacts[$aid]=['id'=>$aid,'job_id'=>$id,'kind'=>'adapter','data'=>$raw,'sha256'=>$sha];
    MSW_Central_Engine_Store::$jobs[$id]['state']='completed';MSW_Central_Engine_Store::$jobs[$id]['result']=['artifact_id'=>$aid,'sha256'=>$sha,'readback_verified'=>true];
    MSW_Central_Engine_Store::$jobs[$id]['lease_until']=0;MSW_Central_Engine_Store::$jobs[$id]['lease_token']='';return $envelope;
}
function admitted_fixture(string $id='offline-test-request-01'): array
{
    $input=input_fixture($id);$admission=start_native($input);complete_native($admission,$input);return $admission;
}
function ack_fixture(array $claim): array
{
    return ['operation'=>'ack','task_id'=>$claim['task']['id'],'request_sha256'=>$claim['task']['request_sha256'],'lease_token'=>$claim['lease_token'],
        'commit_sha'=>str_repeat('c',40),'task_path'=>'msw-coding-tasks/'.$claim['task']['id'].'.json','repository'=>MSW_GCB_Contract::REPOSITORY,'queue_branch'=>MSW_GCB_Contract::BRANCH];
}

$tests=[
    'default_hold_and_connect_preserve_existing_policy'=>function(){
        expect(MSW_GCB_Bridge::status()['state']==='held');expect_fault('transport_held',fn()=>MSW_GCB_Bridge::submit(input_fixture()));expect(count(MSW_Central_Engine_Store::$jobs)===0);
        $r=MSW_GCB_Bridge::connect([]);expect($r['transport_enabled']===false);$s=get_option(MSW_Central_Engine_Core::OPTION);
        expect($s['write_capabilities']===['hub.sync','coding.submit']);expect($s['provider_order']===['existing-provider']);expect($s['private_existing_setting']==='preserve-this');
        expect(MSW_GCB_Bridge::exchange(['operation'=>'probe'],auth_fixture())['state']==='held');
        foreach([null,1,'true'] as $invalid){$before=$GLOBALS['wpdb']->values;expect_fault('connect_fields',fn()=>MSW_GCB_Bridge::connect(['transport_enabled'=>$invalid]));expect($GLOBALS['wpdb']->values===$before);}
    },
    'source_labels_never_grant_authority'=>function(){
        enable_transport();$GLOBALS['current_user']=99;expect_fault('forbidden',fn()=>MSW_GCB_Bridge::submit(input_fixture()));
        $r=MSW_GCB_Bridge::dispatch(null,'coding-submit',input_fixture());expect(is_wp_error($r)&&$r->get_error_code()==='msw_gcb_forbidden');
        expect(count(MSW_Central_Engine_Store::$jobs)===0);$GLOBALS['current_user']=1;expect(!in_array('msw-invalid folder/a.php',MSW_GCB_Bridge::sources(),true));
    },
    'admission_is_native_idempotent_and_read_back'=>function(){
        enable_transport();$a=MSW_GCB_Bridge::submit(input_fixture());$b=MSW_GCB_Bridge::submit(input_fixture());
        expect($a['state']==='admitted'&&$a['central_job_id']===$b['central_job_id']&&$b['duplicate']===true);expect(count(MSW_Central_Engine_Store::$jobs)===1);
        expect(MSW_GCB_Options::read($a['task_id'])===null);$different=input_fixture();$different['objective']='Inna treść';expect_fault('native_admission_rejected',fn()=>MSW_GCB_Bridge::submit($different));
    },
    'pending_native_admission_can_be_read_without_an_outbox'=>function(){
        enable_transport();$a=MSW_GCB_Bridge::submit(input_fixture());$read=MSW_GCB_Bridge::result(['task_id'=>$a['task_id'],'central_job_id'=>$a['central_job_id']]);
        expect($read['state']==='admitted'&&$read['central_state']==='pending'&&$read['delivery']===null);
        expect_fault('task_missing',fn()=>MSW_GCB_Bridge::result(['task_id'=>str_repeat('a',32),'central_job_id'=>$a['central_job_id']]));
        $GLOBALS['current_user']=2;expect_fault('task_missing',fn()=>MSW_GCB_Bridge::result(['task_id'=>$a['task_id'],'central_job_id'=>$a['central_job_id']]));
    },
    'record_requires_real_running_native_job_owner_and_context'=>function(){
        enable_transport();$input=input_fixture();$a=MSW_GCB_Bridge::submit($input);$ctx=['source'=>'msw-central-engine','request_id'=>'msw-ce-'.$a['central_job_id']];
        expect_fault('native_context_required',fn()=>MSW_GCB_Bridge::record($input,['source'=>'plugin','request_id'=>$ctx['request_id']]));
        expect_fault('native_lease_required',fn()=>MSW_GCB_Bridge::record($input,$ctx));
        $a=start_native($input);$GLOBALS['current_user']=2;expect_fault('native_lease_required',fn()=>MSW_GCB_Bridge::record($input,$ctx));$GLOBALS['current_user']=1;
        MSW_Central_Engine_Store::$jobs[$a['central_job_id']]['lease_until']=time()-1;expect_fault('native_lease_required',fn()=>MSW_GCB_Bridge::record($input,$ctx));
        expect(MSW_GCB_Options::read($a['task_id'])===null);
    },
    'existing_registry_clients_are_admitted_without_bypassing_native_execution'=>function(){
        enable_transport();$input=input_fixture();
        $result=MSW_Central_Engine_Registry::execute('coding.submit',$input,'existing-render-capability-request');
        expect(!is_wp_error($result)&&$result['result']['state']==='admitted'&&$result['proof']['verified']===true);
        expect($result['proof']['evidence']['projection_created']===false&&count(MSW_Central_Engine_Store::$jobs)===1);
        expect(MSW_GCB_Options::read($result['result']['task_id'])===null);
        $a=start_native($input);complete_native($a,$input);
        expect(MSW_GCB_Options::read($result['result']['task_id'])['value']['state']==='recorded');
    },
    'native_completed_artifact_is_required_for_github_claim'=>function(){
        enable_transport();$input=input_fixture();$a=start_native($input);MSW_GCB_Bridge::record($input,['source'=>'msw-central-engine','request_id'=>'msw-ce-'.$a['central_job_id']]);
        expect(MSW_GCB_Bridge::exchange(['operation'=>'claim'],auth_fixture())['state']==='idle');complete_native($a,$input);
        $aid=MSW_Central_Engine_Store::$jobs[$a['central_job_id']]['result']['artifact_id'];$raw=MSW_Central_Engine_Store::$artifacts[$aid]['data'];MSW_Central_Engine_Store::$artifacts[$aid]['data']='{}';
        expect(MSW_GCB_Bridge::exchange(['operation'=>'claim'],auth_fixture())['state']==='idle');MSW_Central_Engine_Store::$artifacts[$aid]['data']=$raw;
        expect(MSW_GCB_Bridge::exchange(['operation'=>'claim'],auth_fixture())['state']==='leased');
    },
    'projection_deduplication_rejects_changed_native_identity'=>function(){
        enable_transport();$input=input_fixture();$a=start_native($input);$ctx=['source'=>'msw-central-engine','request_id'=>'msw-ce-'.$a['central_job_id']];
        $one=MSW_GCB_Bridge::record($input,$ctx);$two=MSW_GCB_Bridge::record($input,$ctx);expect($one===$two);
        $id=$one['task_id'];$v=MSW_GCB_Options::read($id)['value'];$v['owner_id']=2;$GLOBALS['wpdb']->values[MSW_GCB_Options::PREFIX.$id]=MSW_GCB_Contract::canonical($v);
        expect_fault('task_identity_conflict',fn()=>MSW_GCB_Bridge::record($input,$ctx));
    },
    'one_task_per_run_and_distinct_run_leases'=>function(){
        enable_transport();admitted_fixture('offline-test-request-01');admitted_fixture('offline-test-request-02');$auth=auth_fixture();
        $one=MSW_GCB_Bridge::exchange(['operation'=>'claim'],$auth);$again=MSW_GCB_Bridge::exchange(['operation'=>'claim'],$auth);expect($one===$again);
        $other=MSW_GCB_Bridge::exchange(['operation'=>'claim'],auth_fixture('9002'));expect($other['task']['id']!==$one['task']['id']);
        expect(strlen($one['lease_token'])===64&&$one['execution_support']==='github_agent_scope');
    },
    'ack_rejects_wrong_identity_before_network'=>function(){
        enable_transport();admitted_fixture();$auth=auth_fixture();$claim=MSW_GCB_Bridge::exchange(['operation'=>'claim'],$auth);$p=ack_fixture($claim);
        foreach(['request_sha256','lease_token'] as $field){$bad=$p;$bad[$field]=str_repeat('f',64);expect_fault('ack_lease',fn()=>MSW_GCB_Bridge::exchange($bad,$auth));}
        foreach(['repository','queue_branch','task_path'] as $field){$bad=$p;$bad[$field]='other';expect_fault('ack_destination',fn()=>MSW_GCB_Bridge::exchange($bad,$auth));}
        expect_fault('ack_lease',fn()=>MSW_GCB_Bridge::exchange($p,auth_fixture('9002')));expect(count($GLOBALS['http_calls'])===0);
    },
    'ack_requires_byte_exact_commit_readback'=>function(){
        enable_transport();$a=admitted_fixture();$auth=auth_fixture();$claim=MSW_GCB_Bridge::exchange(['operation'=>'claim'],$auth);$p=ack_fixture($claim);
        $GLOBALS['http_response']['body']=MSW_GCB_Contract::canonical($claim['task']);expect_fault('github_readback_mismatch',fn()=>MSW_GCB_Bridge::exchange($p,$auth));
        expect(MSW_GCB_Bridge::result(['task_id'=>$a['task_id']])['state']==='leased');$call=$GLOBALS['http_calls'][0];
        expect($call['url']==='https://raw.githubusercontent.com/'.MSW_GCB_Contract::REPOSITORY.'/'.$p['commit_sha'].'/'.$p['task_path']);
        expect($call['args']['redirection']===0&&$call['args']['sslverify']===true&&$call['args']['limit_response_size']===16385);
    },
    'verified_ack_is_idempotent_and_hides_lease'=>function(){
        enable_transport();$a=admitted_fixture();$auth=auth_fixture();$claim=MSW_GCB_Bridge::exchange(['operation'=>'claim'],$auth);$p=ack_fixture($claim);
        $GLOBALS['http_response']['body']=MSW_GCB_Contract::canonical($claim['task'])."\n";$first=MSW_GCB_Bridge::exchange($p,$auth);$second=MSW_GCB_Bridge::exchange($p,$auth);
        expect($first===$second&&$first['state']==='delivered'&&$first['run_id']===$auth['run_id']);expect(count($GLOBALS['http_calls'])===1);
        $read=MSW_GCB_Bridge::result(['task_id'=>$a['task_id']]);expect($read['delivery']['readback_verified']===true&&$read['tests_verified']===false&&$read['deployed_verified']===false);
        expect(!str_contains(json_encode($read),$claim['lease_token']));expect(MSW_GCB_Bridge::exchange(['operation'=>'claim'],$auth)['state']==='idle');
        $p['commit_sha']=str_repeat('d',40);expect_fault('ack_conflict',fn()=>MSW_GCB_Bridge::exchange($p,$auth));
    },
    'hold_during_readback_fences_delivery'=>function(){
        enable_transport();$a=admitted_fixture();$auth=auth_fixture();$claim=MSW_GCB_Bridge::exchange(['operation'=>'claim'],$auth);$p=ack_fixture($claim);
        $GLOBALS['http_response']['body']=MSW_GCB_Contract::canonical($claim['task'])."\n";
        $GLOBALS['during_http']=function(){MSW_GCB_Bridge::connect(['transport_enabled'=>false]);};
        expect_fault('ack_state_conflict',fn()=>MSW_GCB_Bridge::exchange($p,$auth));expect(MSW_GCB_Bridge::result(['task_id'=>$a['task_id']])['state']==='leased');
    },
    'expired_lease_and_revoked_owner_do_not_ack'=>function(){
        enable_transport();$a=admitted_fixture();$auth=auth_fixture();$claim=MSW_GCB_Bridge::exchange(['operation'=>'claim'],$auth);$p=ack_fixture($claim);
        $GLOBALS['allowed_users']=[];expect_fault('ack_expired_or_unadmitted',fn()=>MSW_GCB_Bridge::exchange($p,$auth));$GLOBALS['allowed_users']=[1];
        $row=MSW_GCB_Options::read($a['task_id'])['value'];$row['lease']['expires_at']=time()-1;$GLOBALS['wpdb']->values[MSW_GCB_Options::PREFIX.$a['task_id']]=MSW_GCB_Contract::canonical($row);
        expect_fault('ack_expired_or_unadmitted',fn()=>MSW_GCB_Bridge::exchange($p,$auth));expect(count($GLOBALS['http_calls'])===0);
    },
    'compare_and_swap_rejects_stale_task_and_policy'=>function(){
        $db=$GLOBALS['wpdb'];$db->values['task']='before';$db->values['guard']='open';
        expect(!MSW_GCB_Options::cas('task','stale','after',['guard'=>'open']));expect(!MSW_GCB_Options::cas('task','before','after',['guard'=>'closed']));
        expect(MSW_GCB_Options::cas('task','before','after',['guard'=>'open']));expect($db->values['task']==='after');
        expect(!MSW_GCB_Options::insert_guarded('new','data',['guard'=>'closed']));expect(!isset($db->values['new']));
    },
    'central_pause_fences_claim_and_new_admission'=>function(){
        enable_transport();admitted_fixture();$s=get_option(MSW_Central_Engine_Core::OPTION);$s['enabled']=false;$GLOBALS['wpdb']->values[MSW_Central_Engine_Core::OPTION]=serialize($s);
        expect(MSW_GCB_Bridge::exchange(['operation'=>'claim'],auth_fixture())['state']==='held');expect_fault('transport_held',fn()=>MSW_GCB_Bridge::submit(input_fixture('another-offline-id')));
    },
    'direct_extension_and_route_permissions_remain_bounded'=>function(){
        $tools=MSW_GCB_Bridge::tools([]);expect(array_column($tools,'name')===['coding-status','coding-submit','coding-task','coding-connect']);
        foreach($tools as $tool)expect($tool['schema']['additionalProperties']===false);
        expect(MSW_GCB_Bridge::dispatch(['previous'=>true],'coding-status',[])===['previous'=>true]);
        expect(is_wp_error(MSW_GCB_Bridge::dispatch(null,'coding-status',['extra'=>true])));MSW_GCB_Bridge::routes();
        expect($GLOBALS['routes']['/submit']['permission_callback']===[MSW_GCB_Bridge::class,'admin']);
        expect($GLOBALS['routes']['/exchange']['permission_callback']==='__return_true');
        $request=new class{function get_body(){return '{"operation":"probe"}';}function get_header($name){return '';}};
        $reply=($GLOBALS['routes']['/exchange']['callback'])($request);expect(is_wp_error($reply)&&$reply->get_error_code()==='msw_gcb_oidc_required');expect(count($GLOBALS['http_calls'])===0);
    },
];
$contract=__DIR__.'/contract-tests.php';if(!is_file($contract))throw new RuntimeException('Pure contract test file missing');
$tests=array_merge(require $contract,$tests);$passed=0;$failures=[];
foreach($tests as $name=>$test){reset_ports();try{$test();$passed++;echo "PASS $name\n";}catch(Throwable $e){$failures[]=$name;fwrite(STDERR,"FAIL $name: ".$e->getMessage()."\n");}}
echo json_encode(['offline_tests'=>count($tests),'passed'=>$passed,'failed'=>count($failures),'live_wordpress'=>false,'production_changed'=>false],JSON_UNESCAPED_SLASHES)."\n";
exit($failures?1:0);
