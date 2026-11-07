<?php
/** Existing MSW admission -> durable public transport projection -> immutable GitHub readback. */
defined('ABSPATH') || exit;

final class MSW_GCB_Bridge
{
    public const VERSION='0.1.0';
    public const PLUGIN='msw-github-coding-bridge/msw-github-coding-bridge.php';
    private const CORE_OPTION='msw_central_engine_settings';
    private const NS='msw-github-coding/v1';

    public static function boot(): void
    {
        add_action('rest_api_init',[self::class,'routes']);
        add_action('msw_central_engine_register_capabilities',[self::class,'register']);
        add_filter('mswdc_tool_definitions',[self::class,'tools']);
        add_filter('mswdc_tool_dispatch',[self::class,'dispatch'],10,3);
        add_filter('msw_coding_submit',[self::class,'submit_filter'],10,2);
        add_filter('rest_post_dispatch',[self::class,'private_headers'],999,3);
    }
    public static function admin(): bool { return current_user_can('manage_options'); }
    public static function guarded(callable $callback)
    {
        try{return $callback();}
        catch(MSW_GCB_Fault $e){return new WP_Error('msw_gcb_'.$e->reason,'MSW Coding Bridge: '.$e->reason,['status'=>$e->status]);}
        catch(Throwable $e){return new WP_Error('msw_gcb_internal','MSW Coding Bridge: internal_error',['status'=>503]);}
    }
    private static function must_admin(): void { if(!self::admin())MSW_GCB_Contract::fail('forbidden',403); }
    private static function central_available(): bool
    {
        return is_callable(['MSW_Central_Engine_Store','enqueue']) && is_callable(['MSW_Central_Engine_Store','get_job']) &&
            is_callable(['MSW_Central_Engine_Store','live']) && is_callable(['MSW_Central_Engine_Store','artifact']) &&
            is_callable(['MSW_Central_Engine_Workflows','validate']) && is_callable(['MSW_Central_Engine_Registry','catalog']) &&
            is_callable(['MSW_Central_Engine_Core','defaults']);
    }
    public static function sources(): array
    {
        $sources=['chatgpt','render','python-hub'];
        $active=(array)get_option('active_plugins',[]);
        if(function_exists('is_multisite')&&is_multisite())$active=array_merge($active,array_keys((array)get_site_option('active_sitewide_plugins',[])));
        foreach($active as $id)if(is_string($id)&&str_starts_with($id,'msw-')&&preg_match('~\A[A-Za-z0-9_./:-]{1,200}\z~D',$id))$sources[]=$id;
        return array_values(array_unique($sources));
    }
    private static function core_settings(): array
    {
        if(!self::central_available())MSW_GCB_Contract::fail('central_unavailable',503);
        $raw=MSW_GCB_Options::raw(self::CORE_OPTION);
        if($raw===null)MSW_GCB_Contract::fail('central_policy_missing',503);
        $stored=@unserialize($raw,['allowed_classes'=>false]);
        if(!is_array($stored))MSW_GCB_Contract::fail('central_policy_invalid',503);
        return ['raw'=>$raw,'stored'=>$stored,'settings'=>array_merge(MSW_Central_Engine_Core::defaults(),$stored)];
    }
    private static function policy(): array
    {
        $raw=MSW_GCB_Options::raw(MSW_GCB_Options::POLICY);
        $value=$raw===null?['version'=>1,'transport_enabled'=>false]:MSW_GCB_Contract::json($raw,1024);
        if(($value['version']??null)!==1 || !is_bool($value['transport_enabled']??null))MSW_GCB_Contract::fail('policy_invalid',503);
        return ['raw'=>$raw,'value'=>$value];
    }
    private static function guards(): array
    {
        $core=self::core_settings();$policy=self::policy();$s=$core['settings'];
        if($policy['raw']===null || $policy['value']['transport_enabled']!==true || ($s['enabled']??null)!==true ||
            !is_array($s['write_capabilities']??null) || !in_array('coding.submit',$s['write_capabilities'],true))MSW_GCB_Contract::fail('transport_held',409);
        return [MSW_GCB_Options::POLICY=>$policy['raw'],self::CORE_OPTION=>$core['raw']];
    }
    private static function native_matches(array $job,array $input,int $owner): bool
    {
        return (int)($job['id']??0)>0 && (int)($job['owner_id']??0)===$owner && ($job['type']??'')==='adapter.run' &&
            ($job['input']['capability']??'')==='coding.submit' && is_array($job['input']['arguments']??null) &&
            hash_equals(hash('sha256',MSW_GCB_Contract::canonical($input)),hash('sha256',MSW_GCB_Contract::canonical($job['input']['arguments'])));
    }
    public static function submit(array $input): array
    {
        self::must_admin();self::guards();$input=MSW_GCB_Contract::input($input,self::sources());
        $owner=(int)get_current_user_id();$id=MSW_GCB_Contract::id($input);
        $native=['capability'=>'coding.submit','arguments'=>$input];
        $valid=MSW_Central_Engine_Workflows::validate('adapter.run',$native);
        if(is_wp_error($valid))MSW_GCB_Contract::fail('native_admission_validation',409);
        $job=MSW_Central_Engine_Store::enqueue('adapter.run',$native,'msw-coding-'.$id,$owner);
        if(is_wp_error($job))MSW_GCB_Contract::fail('native_admission_rejected',(int)($job->get_error_data()['status']??503));
        if(!is_array($job)||!self::native_matches($job,$input,$owner))MSW_GCB_Contract::fail('native_admission_readback',503);
        $read=MSW_Central_Engine_Store::get_job($job['id']);
        if(!is_array($read)||!self::native_matches($read,$input,$owner))MSW_GCB_Contract::fail('native_admission_readback',503);
        return ['schema'=>'msw.coding-admission.v1','state'=>'admitted','task_id'=>$id,'central_job_id'=>$job['id'],
            'central_state'=>$read['state'],'duplicate'=>!empty($job['duplicate']),'code_executed'=>false,'production_changed'=>false];
    }
    public static function capability(array $input,array $context): array
    {
        // Existing authorized registry clients enter the same native queue.
        // Only the native adapter.run context may materialize its projection.
        if(($context['source']??'')!=='msw-central-engine'||!is_string($context['request_id']??null)||
            !preg_match('/\A[A-Za-z0-9_.:-]{1,200}\z/D',$context['request_id']))MSW_GCB_Contract::fail('native_context_required',403);
        return preg_match('/\Amsw-ce-[1-9][0-9]{0,17}\z/D',$context['request_id'])?self::record($input,$context):self::submit($input);
    }
    public static function verify_effect(array $input,array $result): array
    {
        if(($result['schema']??'')!=='msw.coding-admission.v1')return self::verify_record($input,$result);
        $id=MSW_GCB_Contract::id($input);$job=MSW_Central_Engine_Store::get_job((int)($result['central_job_id']??0));
        $ok=is_array($job)&&self::native_matches($job,$input,(int)get_current_user_id())&&($result['task_id']??'')===$id&&($result['state']??'')==='admitted';
        return ['verified'=>$ok,'evidence'=>['task_id'=>$id,'central_job_id'=>$job['id']??null,'native_admission_readback_verified'=>$ok,'projection_created'=>false,'coding_executed'=>false]];
    }
    public static function record(array $input,array $context): array
    {
        self::must_admin();$guards=self::guards();$input=MSW_GCB_Contract::input($input,self::sources());
        if(($context['source']??'')!=='msw-central-engine' || !preg_match('/\Amsw-ce-([1-9][0-9]{0,17})\z/D',(string)($context['request_id']??''),$match))MSW_GCB_Contract::fail('native_context_required',403);
        $job=MSW_Central_Engine_Store::get_job((int)$match[1]);$owner=(int)get_current_user_id();
        if(!is_array($job)||!self::native_matches($job,$input,$owner)||($job['state']??'')!=='running'||
            (int)($job['lease_until']??0)<=time()||!is_string($job['lease_token']??null)||$job['lease_token']===''||!MSW_Central_Engine_Store::live($job))MSW_GCB_Contract::fail('native_lease_required',409);
        $task=MSW_GCB_Contract::task($input,(int)$job['created_at']);$id=$task['id'];
        $value=['schema'=>'msw.coding-outbox.v1','state'=>'recorded','task'=>$task,'central_job_id'=>$job['id'],'owner_id'=>$owner,'lease'=>null,'delivery'=>null];
        $raw=MSW_GCB_Contract::canonical($value);
        MSW_GCB_Options::insert_guarded(MSW_GCB_Options::PREFIX.$id,$raw,$guards);
        $read=MSW_GCB_Options::read($id);
        if(!$read)MSW_GCB_Contract::fail('projection_not_recorded',409);
        $v=$read['value'];
        if(($v['central_job_id']??null)!==$job['id']||($v['owner_id']??null)!==$owner||MSW_GCB_Contract::canonical($v['task']??[])!==MSW_GCB_Contract::canonical($task))MSW_GCB_Contract::fail('task_identity_conflict',409);
        return ['schema'=>'msw.coding-recorded.v1','state'=>'recorded','task_id'=>$id,'request_sha256'=>$task['request_sha256'],'central_job_id'=>$job['id']];
    }
    public static function verify_record(array $input,array $result): array
    {
        $id=MSW_GCB_Contract::id($input);$row=MSW_GCB_Options::read($id);$v=$row['value']??[];
        $ok=($result['task_id']??'')===$id && ($v['central_job_id']??0)===($result['central_job_id']??-1) &&
            ($v['task']['request_sha256']??'')===($result['request_sha256']??null) &&
            MSW_GCB_Contract::canonical(array_intersect_key($v['task']??[],array_flip(MSW_GCB_Contract::INPUT_KEYS)))===MSW_GCB_Contract::canonical($input);
        return ['verified'=>$ok,'evidence'=>['task_id'=>$id,'central_job_id'=>$v['central_job_id']??null,'projection_readback_verified'=>$ok,'coding_executed'=>false]];
    }
    private static function native_completed(array $row): bool
    {
        if(!self::central_available()||!user_can((int)($row['owner_id']??0),'manage_options'))return false;
        $job=MSW_Central_Engine_Store::get_job((int)($row['central_job_id']??0));
        $input=array_intersect_key($row['task']??[],array_flip(MSW_GCB_Contract::INPUT_KEYS));
        if(!is_array($job)||!self::native_matches($job,$input,(int)$row['owner_id'])||($job['state']??'')!=='completed'||($job['result']['readback_verified']??null)!==true)return false;
        $id='ce-'.$job['id'].'-'.substr(hash('sha256','adapter'),0,16);
        if(($job['result']['artifact_id']??'')!==$id)return false;
        $a=MSW_Central_Engine_Store::artifact($id);$envelope=$a['data']['result']??[];
        if(!is_array($a)||($a['job_id']??0)!==$job['id']||($a['kind']??'')!=='adapter'||($a['readback_verified']??null)!==true||
            !is_string($a['sha256']??null)||($job['result']['sha256']??null)!==$a['sha256']||($a['data']['capability']??'')!=='coding.submit'||
            ($envelope['capability']??'')!=='coding.submit'||($envelope['effect']??'')!=='write'||($envelope['proof']['verified']??null)!==true)return false;
        $result=$envelope['result']??[];
        return ($result['schema']??'')==='msw.coding-recorded.v1'&&($result['state']??'')==='recorded'&&
            ($result['task_id']??'')===($row['task']['id']??null)&&($result['central_job_id']??0)===$job['id']&&
            ($result['request_sha256']??'')===($row['task']['request_sha256']??null);
    }
    private static function envelope(array $auth,array $data): array { return ['schema'=>'msw.coding-exchange.v1','ok'=>true,'run_id'=>$auth['run_id']]+$data; }
    private static function run_key(array $auth): string { return 'msw_gcb_run_'.$auth['run_id'].'_'.$auth['run_attempt']; }
    private static function lease_response(array $auth,array $row): array
    {
        $lease=$row['lease']??[];
        if(($row['state']??'')==='delivered')return self::envelope($auth,['state'=>'idle']);
        if(($row['state']??'')!=='leased'||($lease['run_id']??'')!==$auth['run_id']||($lease['run_attempt']??'')!==$auth['run_attempt']||($lease['expires_at']??0)<=time())MSW_GCB_Contract::fail('run_lease_unavailable',409);
        return self::envelope($auth,['state'=>'leased','task'=>$row['task'],'lease_token'=>$lease['token'],
            'lease_expires_at'=>gmdate('Y-m-d\TH:i:s\Z',$lease['expires_at']),'execution_support'=>MSW_GCB_Contract::TARGETS[$row['task']['target']]['support']]);
    }
    public static function exchange(array $request,array $auth): array
    {
        $op=$request['operation']??null;
        if(!in_array($op,['probe','claim','ack'],true))MSW_GCB_Contract::fail('operation');
        if($op!=='ack')MSW_GCB_Contract::keys($request,['operation']);
        try{$guards=self::guards();}catch(MSW_GCB_Fault $e){
            if($e->reason==='transport_held')return self::envelope($auth,['state'=>'held']);throw $e;
        }
        if($op==='probe')return self::envelope($auth,['state'=>'connected','version'=>self::VERSION]);
        if($op==='ack')return self::ack($request,$auth,$guards);
        $run=self::run_key($auth);$oldRun=MSW_GCB_Options::raw($run);
        if($oldRun!==null){
            $known=MSW_GCB_Options::read($oldRun);
            if(!$known||!self::native_completed($known['value']))MSW_GCB_Contract::fail('native_admission_unverified',409);
            return self::lease_response($auth,$known['value']);
        }
        foreach(MSW_GCB_Options::pending() as $entry){
            $before=$entry['option_value'];$row=MSW_GCB_Contract::json($before);
            if(!self::native_completed($row))continue;
            if(($row['state']??'')==='leased'&&($row['lease']['expires_at']??0)>time())continue;
            if(!in_array($row['state']??'',['recorded','leased'],true))continue;
            $row['state']='leased';$row['lease']=['token'=>bin2hex(random_bytes(32)),'run_id'=>$auth['run_id'],'run_attempt'=>$auth['run_attempt'],
                'expires_at'=>min(time()+300,$auth['expires_at'])];
            if($row['lease']['expires_at']<=time()+5)MSW_GCB_Contract::fail('oidc_near_expiry',401);
            $after=MSW_GCB_Contract::canonical($row);$id=$row['task']['id'];
            if(!MSW_GCB_Options::cas(MSW_GCB_Options::PREFIX.$id,$before,$after,$guards))continue;
            if(!MSW_GCB_Options::insert($run,$id)&&MSW_GCB_Options::raw($run)!==$id){
                MSW_GCB_Options::cas(MSW_GCB_Options::PREFIX.$id,$after,$before);
                MSW_GCB_Contract::fail('run_claim_conflict',409);
            }
            return self::lease_response($auth,$row);
        }
        return self::envelope($auth,['state'=>'idle']);
    }
    private static function ack(array $p,array $auth,array $guards): array
    {
        MSW_GCB_Contract::keys($p,['operation','task_id','request_sha256','lease_token','commit_sha','task_path','repository','queue_branch']);
        foreach(['task_id'=>32,'request_sha256'=>64,'lease_token'=>64,'commit_sha'=>40] as $key=>$n)if(!is_string($p[$key])||!preg_match('/\A[a-f0-9]{'.$n.'}\z/D',$p[$key]))MSW_GCB_Contract::fail('ack_identity');
        if($p['repository']!==MSW_GCB_Contract::REPOSITORY||$p['queue_branch']!==MSW_GCB_Contract::BRANCH||$p['task_path']!=='msw-coding-tasks/'.$p['task_id'].'.json')MSW_GCB_Contract::fail('ack_destination');
        $read=MSW_GCB_Options::read($p['task_id']);if(!$read)MSW_GCB_Contract::fail('task_missing',404);$row=$read['value'];$lease=$row['lease']??[];
        if(($row['task']['request_sha256']??'')!==$p['request_sha256']||($lease['run_id']??'')!==$auth['run_id']||($lease['run_attempt']??'')!==$auth['run_attempt']||
            !is_string($lease['token']??null)||!hash_equals($lease['token'],$p['lease_token'])||MSW_GCB_Options::raw(self::run_key($auth))!==$p['task_id'])MSW_GCB_Contract::fail('ack_lease',409);
        $identity=array_intersect_key($p,array_flip(['task_id','request_sha256','commit_sha','task_path','repository','queue_branch']));
        if(($row['state']??'')==='delivered'){
            foreach($identity as $k=>$v)if(($row['delivery'][$k]??null)!==$v)MSW_GCB_Contract::fail('ack_conflict',409);
            return self::envelope($auth,['state'=>'delivered']+$identity);
        }
        if(($row['state']??'')!=='leased'||($lease['expires_at']??0)<=time()||!self::native_completed($row))MSW_GCB_Contract::fail('ack_expired_or_unadmitted',409);
        $raw=self::http('https://raw.githubusercontent.com/'.MSW_GCB_Contract::REPOSITORY.'/'.$p['commit_sha'].'/'.$p['task_path'],16384);
        $expected=MSW_GCB_Contract::canonical($row['task'])."\n";
        if(!hash_equals(hash('sha256',$expected),hash('sha256',$raw)))MSW_GCB_Contract::fail('github_readback_mismatch',409);
        if(($lease['expires_at']??0)<=time())MSW_GCB_Contract::fail('ack_expired_or_unadmitted',409);
        $row['state']='delivered';$row['delivery']=$identity+['file_sha256'=>hash('sha256',$raw),'readback_verified'=>true,'received_at'=>gmdate('Y-m-d\TH:i:s\Z')];
        $after=MSW_GCB_Contract::canonical($row);
        if(!MSW_GCB_Options::cas(MSW_GCB_Options::PREFIX.$p['task_id'],$read['raw'],$after,$guards))MSW_GCB_Contract::fail('ack_state_conflict',409);
        $verified=MSW_GCB_Options::read($p['task_id']);
        if(!$verified||$verified['raw']!==$after)MSW_GCB_Contract::fail('ack_storage_readback',503);
        return self::envelope($auth,['state'=>'delivered']+$identity);
    }
    private static function http(string $url,int $limit): string
    {
        $r=wp_safe_remote_get($url,['timeout'=>12,'redirection'=>0,'sslverify'=>true,'reject_unsafe_urls'=>true,
            'limit_response_size'=>$limit+1,'headers'=>['Accept'=>'application/json','Cache-Control'=>'no-cache','User-Agent'=>'MSW-Coding-Bridge/0.1']]);
        if(is_wp_error($r))MSW_GCB_Contract::fail('remote_unavailable',503);
        $status=(int)wp_remote_retrieve_response_code($r);$raw=(string)wp_remote_retrieve_body($r);
        if($status!==200||strlen($raw)>$limit)MSW_GCB_Contract::fail('remote_readback_unavailable',503);
        return $raw;
    }
    public static function authenticate($request): array
    {
        $header=(string)$request->get_header('authorization');
        if(strlen($header)>24583||!preg_match('/\ABearer ([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\z/D',$header,$match))MSW_GCB_Contract::fail('oidc_required',401);
        $jwks=get_transient('msw_gcb_github_jwks_v1');
        if(!is_array($jwks)){$jwks=MSW_GCB_Contract::json(self::http(MSW_GCB_Contract::JWKS,65536),65536);set_transient('msw_gcb_github_jwks_v1',$jwks,300);}
        return MSW_GCB_Contract::oidc($match[1],$jwks,time());
    }
    public static function connect(array $args): array
    {
        self::must_admin();if(array_diff(array_keys($args),['transport_enabled'])||(array_key_exists('transport_enabled',$args)&&!is_bool($args['transport_enabled'])))MSW_GCB_Contract::fail('connect_fields');
        $core=self::core_settings();$catalog=MSW_Central_Engine_Registry::catalog();
        if(($catalog['coding.submit']['plugin']??'')!==self::PLUGIN)MSW_GCB_Contract::fail('capability_conflict',409);
        $settings=$core['stored'];$caps=$core['settings']['write_capabilities']??[];
        if(!is_array($caps))MSW_GCB_Contract::fail('central_policy_invalid',503);
        if(!in_array('coding.submit',$caps,true))$caps[]='coding.submit';$settings['write_capabilities']=$caps;$raw=serialize($settings);
        if($core['raw']!==$raw&&!MSW_GCB_Options::cas(self::CORE_OPTION,$core['raw'],$raw))MSW_GCB_Contract::fail('central_policy_conflict',409);
        $old=self::policy();$value=$old['value'];if(array_key_exists('transport_enabled',$args))$value['transport_enabled']=$args['transport_enabled'];
        $desired=MSW_GCB_Contract::canonical($value);
        if($old['raw']===null){if(!MSW_GCB_Options::insert(MSW_GCB_Options::POLICY,$desired))MSW_GCB_Contract::fail('policy_conflict',409);}
        elseif($old['raw']!==$desired&&!MSW_GCB_Options::cas(MSW_GCB_Options::POLICY,$old['raw'],$desired))MSW_GCB_Contract::fail('policy_conflict',409);
        if(MSW_GCB_Options::raw(self::CORE_OPTION)!==$raw||MSW_GCB_Options::raw(MSW_GCB_Options::POLICY)!==$desired)MSW_GCB_Contract::fail('policy_readback',503);
        return ['connected'=>true,'version'=>self::VERSION,'transport_enabled'=>$value['transport_enabled'],'policy_readback_verified'=>true,'additional_cron_created'=>false,'production_code_deployed'=>false];
    }
    public static function status(): array
    {
        self::must_admin();$policy=self::policy();$state='held';$central=self::central_available();
        if($central){try{self::guards();$state='enabled';}catch(MSW_GCB_Fault $e){if($e->reason!=='transport_held')$state='unavailable';}}
        return ['schema'=>'msw.coding-status.v1','version'=>self::VERSION,'contract_status'=>'proposed_not_agreed_not_deployed','state'=>$state,
            'transport_enabled'=>$policy['value']['transport_enabled'],'central_available'=>$central,'repository'=>MSW_GCB_Contract::REPOSITORY,'queue_branch'=>MSW_GCB_Contract::BRANCH,
            'sources'=>self::sources(),'targets'=>MSW_GCB_Contract::TARGETS,'coding_execution_verified'=>false,'deployment_verified'=>false,'scheduler_created'=>false];
    }
    private static function public_row(array $row): array
    {
        return ['task'=>$row['task']??null,'state'=>$row['state']??'unknown','central_job_id'=>$row['central_job_id']??null,'delivery'=>$row['delivery']??null,
            'coding_result_verified'=>false,'tests_verified'=>false,'deployed_verified'=>false];
    }
    public static function result(array $args): array
    {
        self::must_admin();
        if(array_diff(array_keys($args),['task_id','central_job_id'])||!is_string($args['task_id']??null))MSW_GCB_Contract::fail('task_id');
        if(array_key_exists('central_job_id',$args)&&(!is_int($args['central_job_id'])||$args['central_job_id']<1))MSW_GCB_Contract::fail('central_job_id');
        $row=MSW_GCB_Options::read($args['task_id']);
        if(!$row){
            $job=isset($args['central_job_id'])&&self::central_available()?MSW_Central_Engine_Store::get_job($args['central_job_id']):null;
            $input=$job['input']['arguments']??null;
            if(!is_array($job)||!is_array($input)||!isset($input['source_engine'],$input['request_id'])||
                !self::native_matches($job,$input,(int)get_current_user_id())||MSW_GCB_Contract::id($input)!==$args['task_id'])MSW_GCB_Contract::fail('task_missing',404);
            return ['task_id'=>$args['task_id'],'state'=>'admitted','central_job_id'=>$job['id'],'central_state'=>$job['state'],
                'delivery'=>null,'coding_result_verified'=>false,'tests_verified'=>false,'deployed_verified'=>false];
        }
        if(isset($args['central_job_id'])&&($row['value']['central_job_id']??0)!==$args['central_job_id'])MSW_GCB_Contract::fail('task_missing',404);
        return self::public_row($row['value']);
    }
    public static function listing(): array
    {
        self::must_admin();return ['tasks'=>array_map(static fn($r)=>self::public_row(MSW_GCB_Contract::json($r['option_value'])),MSW_GCB_Options::rows())];
    }
    public static function input_schema(): array
    {
        return ['type'=>'object','additionalProperties'=>false,'required'=>MSW_GCB_Contract::INPUT_KEYS,'properties'=>[
            'request_id'=>['type'=>'string','pattern'=>'^[A-Za-z0-9_.:-]{8,128}$'],'source_engine'=>['type'=>'string','minLength'=>1,'maxLength'=>200],
            'target'=>['type'=>'string','enum'=>array_keys(MSW_GCB_Contract::TARGETS)],'title'=>['type'=>'string','minLength'=>1,'maxLength'=>120],
            'objective'=>['type'=>'string','minLength'=>1,'maxLength'=>4000],'acceptance'=>['type'=>'array','minItems'=>1,'maxItems'=>8,'items'=>['type'=>'string','minLength'=>1,'maxLength'=>400]],
            'public_data'=>['type'=>'boolean','enum'=>[true]]]];
    }
    private static function result_schema(): array { return ['type'=>'object','additionalProperties'=>false,'required'=>['task_id'],'properties'=>[
        'task_id'=>['type'=>'string','pattern'=>'^[a-f0-9]{32}$'],'central_job_id'=>['type'=>'integer','minimum'=>1]]]; }
    private static function empty_schema(): array { return ['type'=>'object','additionalProperties'=>false,'properties'=>new stdClass()]; }
    public static function register(): void
    {
        if(!is_callable(['MSW_Central_Engine_Registry','register']))return;
        $common=['plugin'=>self::PLUGIN,'permission_callback'=>[self::class,'admin']];
        MSW_Central_Engine_Registry::register('coding.status',$common+['label'=>'MSW GitHub — stan transportu','effect'=>'read','input_schema'=>self::empty_schema(),'callback'=>static fn()=>self::guarded(fn()=>self::status())]);
        MSW_Central_Engine_Registry::register('coding.result',$common+['label'=>'MSW GitHub — odczyt potwierdzenia','effect'=>'read','input_schema'=>self::result_schema(),'callback'=>static fn($a)=>self::guarded(fn()=>self::result($a))]);
        MSW_Central_Engine_Registry::register('coding.submit',$common+['label'=>'MSW GitHub — transport publicznego zadania','effect'=>'write','input_schema'=>self::input_schema(),
            'callback'=>static fn($a,$ctx)=>self::guarded(fn()=>self::capability($a,$ctx)),'verify'=>static fn($a,$r)=>self::guarded(fn()=>self::verify_effect($a,$r))]);
    }
    public static function tools(array $tools): array
    {
        $definitions=[['coding-status','Stan przygotowanego połączenia GitHub',self::empty_schema(),true],
            ['coding-submit','Przyjmij publiczne zadanie przez istniejącą kolejkę MSW',self::input_schema(),false],
            ['coding-task','Odczytaj projekcję i niezależny ACK zadania',self::result_schema(),true],
            ['coding-connect','Połącz capability; transport pozostaje HOLD bez jawnego transport_enabled=true',['type'=>'object','additionalProperties'=>false,'properties'=>['transport_enabled'=>['type'=>'boolean']]],false]];
        foreach($definitions as [$name,$description,$schema,$read]){
            foreach($tools as $tool)if(($tool['name']??'')===$name)continue 2;
            $tools[]=['name'=>$name,'description'=>$description,'schema'=>$schema,'readonly'=>$read];
        }
        return $tools;
    }
    public static function dispatch($previous,string $op,array $args)
    {
        if($previous!==null||!in_array($op,['coding-status','coding-submit','coding-task','coding-connect'],true))return $previous;
        return self::guarded(static function()use($op,$args){
            self::must_admin();return match($op){'coding-status'=>self::status_args($args),'coding-submit'=>self::submit($args),'coding-task'=>self::result($args),'coding-connect'=>self::connect($args)};
        });
    }
    private static function status_args(array $args): array { MSW_GCB_Contract::keys($args,[]);return self::status(); }
    public static function submit_filter($previous,$input)
    {
        if($previous!==null)return $previous;
        return self::guarded(static function()use($input){if(!is_array($input))MSW_GCB_Contract::fail('fields');return self::submit($input);});
    }
    public static function routes(): void
    {
        foreach(['/status'=>static fn()=>self::status(),'/tasks'=>static fn()=>self::listing()] as $path=>$fn)register_rest_route(self::NS,$path,['methods'=>'GET','permission_callback'=>[self::class,'admin'],'callback'=>static fn()=>self::guarded($fn)]);
        register_rest_route(self::NS,'/tasks/(?P<task_id>[a-f0-9]{32})',['methods'=>'GET','permission_callback'=>[self::class,'admin'],'callback'=>static fn($r)=>self::guarded(static function()use($r){
            $args=['task_id'=>(string)$r['task_id']];$native=$r->get_param('central_job_id');
            if($native!==null){if(!is_scalar($native)||!preg_match('/\A[1-9][0-9]{0,17}\z/D',(string)$native))MSW_GCB_Contract::fail('central_job_id');$args['central_job_id']=(int)$native;}
            return self::result($args);
        })]);
        foreach(['/submit'=>'submit','/connect'=>'connect'] as $path=>$method)register_rest_route(self::NS,$path,['methods'=>'POST','permission_callback'=>[self::class,'admin'],'callback'=>static fn($r)=>self::guarded(fn()=>self::{$method}(MSW_GCB_Contract::json((string)$r->get_body(),16384)))]);
        register_rest_route(self::NS,'/exchange',['methods'=>'POST','permission_callback'=>'__return_true','callback'=>static fn($r)=>self::guarded(static function()use($r){
            $p=MSW_GCB_Contract::json((string)$r->get_body(),4096);$auth=self::authenticate($r);return self::exchange($p,$auth);
        })]);
    }
    public static function private_headers($response,$server,$request)
    {
        if(str_starts_with($request->get_route(),'/'.self::NS.'/')&&is_object($response)&&method_exists($response,'header')){
            $response->header('Cache-Control','private, no-store, max-age=0');$response->header('Vary','Authorization, Cookie');$response->header('X-Content-Type-Options','nosniff');
        }
        return $response;
    }
}
