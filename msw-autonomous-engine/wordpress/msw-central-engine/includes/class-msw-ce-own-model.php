<?php
defined('ABSPATH') || exit;

/** One reviewed own model on the existing Python worker, with private checkpoints and bounded continuation. */
final class MSW_BDir_Own_Model_20261105 {
    const STATE='msw_bdir_own_model_v24';
    public static function manifest():array {return require dirname(__DIR__).'/assets/own-model-manifest.php';}
    public static function boot():void {add_action('msw_central_engine_seed',[self::class,'seed']);}
    private static function permitted():bool {
        if(!MSW_Business_Director::central()||!class_exists('MSW\\UnifiedEngine\\RemoteStore'))return false;
        $d=MSW_Business_Director::settings();$c=MSW_Central_Engine_Core::settings();
        return !empty($d['enabled'])&&!empty($c['enabled'])&&!empty($c['autonomy_enabled'])&&user_can((int)$d['owner_id'],'manage_options')&&\MSW\UnifiedEngine\RemoteStore::ready();
    }
    private static function grant():bool {
        $config=\MSW\UnifiedEngine\RemoteStore::config();$node='msw-render-central-1';
        if(empty($config['enabled'])||!isset($config['nodes'][$node]))return false;
        $kinds=(array)($config['nodes'][$node]['kinds']??[]);
        if(!in_array('llm.generate',$kinds,true)){
            $config['nodes'][$node]['kinds']=array_values(array_unique(array_merge($kinds,['llm.generate'])));
            update_option('mswue_remote_v1',$config,false);
        }
        return in_array('llm.generate',(array)(\MSW\UnifiedEngine\RemoteStore::config()['nodes'][$node]['kinds']??[]),true);
    }
    private static function enqueue(array $payload,string $key) {
        $payload=['schema'=>'msw.own-model.job.v1','owner_authorized'=>true]+$payload;
        $job=\MSW\UnifiedEngine\RemoteStore::enqueue('llm.generate',$payload,$key);
        return is_wp_error($job)?null:['id'=>$job['job_id'],'payload_sha256'=>$job['payload_sha256']];
    }
    public static function acceptParts($parts) {
        if(!MSW_Business_Director::allowed()||!self::permitted()||!self::grant()||!is_array($parts)||count($parts)>3)return new WP_Error('msw_model_parts_denied','Nieprawidłowa część modelu.');
        $manifest=self::manifest();$validated=[];
        foreach($parts as$i=>$encoded){
            if(!preg_match('/^[0-9]+$/D',(string)$i)||!isset($manifest['parts'][(int)$i])||!is_string($encoded)||strlen($encoded)>220000)return new WP_Error('msw_model_part_index','Nieznana część modelu.');
            $raw=base64_decode($encoded,true);$pin=$manifest['parts'][(int)$i];
            if(!is_string($raw)||strlen($raw)!==$pin['bytes']||!hash_equals($pin['sha256'],hash('sha256',$raw)))return new WP_Error('msw_model_part_digest','Nie zgadza się SHA256 danych modelu.');
            $validated[(int)$i]=$encoded;
        }
        $s=(array)get_option(self::STATE,[]);
        foreach($validated as$i=>$encoded){
            $request='owner-model-checkpoint-'.$manifest['sha256'].'-'.$i;
            $job=self::enqueue(['action'=>'checkpoint_part','request_id'=>$request,'part'=>$i,'data_b64'=>$encoded],$request);
            if(!$job)return new WP_Error('msw_model_part_queue','Nie potwierdzono zapisu części.');
            $s['parts'][$i]=$job;
        }
        update_option(self::STATE,$s,false);
        return ['sha256_verified'=>true,'parts_queued'=>count((array)$s['parts']),'parts_total'=>count($manifest['parts'])];
    }
    public static function seed():array {
        if(!self::permitted()||!self::grant())return['state'=>'waiting_for_authorized_existing_worker'];
        $seen=(array)get_option('mswue_remote_seen_v1',[]);
        if(!in_array('llm.generate',(array)($seen['msw-render-central-1']['capabilities']??[]),true))return['state'=>'waiting_for_reviewed_python_model_runtime'];
        $s=(array)get_option(self::STATE,[]);
        $ready=false;$verified=0;foreach((array)($s['parts']??[])as$part){$r=self::receipt($part);if(!empty($r['staged']))$verified++;if(!empty($r['checkpoint_ready']))$ready=true;}
        if(!$ready||$verified!==count(self::manifest()['parts']))return['state'=>'waiting_for_verified_private_checkpoint','parts_verified'=>$verified];
        if(empty($s['status']))$s['status']=self::enqueue(['action'=>'status','request_id'=>'owner-model-status-v24'],'owner-model-status-v24');
        if(empty($s['generate']))$s['generate']=self::enqueue(['action'=>'generate','request_id'=>'owner-model-generation-v24','prompt'=>'Cel MSW AI: praktyczne narzędzia i porządkowanie pracy firmy serwisowej.','max_new_tokens'=>96],'owner-model-generation-v24');
        $last=self::receipt((array)($s['training']??[]));
        // A pending or uncertain training job must be reconciled by its original
        // result receipt. Never enqueue a replacement with another request id.
        if(!empty($s['training'])&&$last===null){
            update_option(self::STATE,$s,false);
            return ['state'=>'waiting_for_previous_training_receipt'];
        }
        // The one-time owner-model-status-v24 result is a historical baseline,
        // not the current approved research checkpoint. Ask the *existing*
        // signed worker to read current active.json before each new UTC slot.
        $slot=gmdate('YmdH');
        $freshJob=(array)($s['fresh_status']??[]);
        if($freshJob&&self::receipt($freshJob)===null){
            update_option(self::STATE,$s,false);
            return ['state'=>'waiting_for_fresh_checkpoint_receipt'];
        }
        if(($s['fresh_status_slot']??'')!==$slot){
            $request='owner-model-current-status-'.$slot;
            $job=self::enqueue(['action'=>'status','request_id'=>$request],$request);
            if(!$job){
                update_option(self::STATE,$s,false);
                return ['state'=>'fresh_checkpoint_status_enqueue_failed'];
            }
            $s['fresh_status']=$job;$s['fresh_status_slot']=$slot;
            update_option(self::STATE,$s,false);
            return ['state'=>'fresh_checkpoint_status_queued'];
        }
        $fresh=self::receipt((array)($s['fresh_status']??[]));
        if(!is_array($fresh)||($fresh['action']??'')!=='status'
            ||($fresh['training_performed']??null)!==false
            ||!preg_match('/^[a-f0-9]{64}$/D',(string)($fresh['checkpoint_sha256']??''))){
            update_option(self::STATE,$s,false);
            return ['state'=>'fresh_checkpoint_status_invalid'];
        }
        $parent=(string)$fresh['checkpoint_sha256'];
        if(($s['training_slot']??'')!==$slot){
            $request='owner-model-learn-'.$slot.'-'.substr($parent,0,16);
            $job=self::enqueue(['action'=>'train','request_id'=>$request,'parent_sha256'=>$parent,'steps'=>32],$request);
            if($job){$s['training']=$job;$s['training_slot']=$slot;}
        }
        update_option(self::STATE,$s,false);
        return['state'=>'existing_python_queue','jobs'=>count(array_filter($s,'is_array'))];
    }
    public static function receipt(array $job):?array {
        if(empty($job['id'])||!class_exists('MSW\\UnifiedEngine\\RemoteStore'))return null;
        global$wpdb;
        $row=$wpdb->get_row($wpdb->prepare('SELECT state,kind,payload_sha256,result,result_sha256 FROM '.\MSW\UnifiedEngine\RemoteStore::table().' WHERE id=%s',$job['id']),ARRAY_A);
        if(!$row||$row['state']!=='completed'||$row['kind']!=='llm.generate'||!hash_equals((string)$job['payload_sha256'],(string)$row['payload_sha256'])||!hash_equals((string)$row['result_sha256'],hash('sha256',(string)$row['result'])))return null;
        $r=json_decode($row['result'],true);$d=$r['data']??null;
        return ($r['ok']??false)===true&&is_array($d)&&($d['provider_ref']??'')==='MSW-DualMemory-byte-v1'&&($d['research_only']??false)===true?$d:null;
    }
    public static function status():array {
        $s=(array)get_option(self::STATE,[]);$results=[];
        $ready=false;$verified=0;foreach((array)($s['parts']??[])as$part){$r=self::receipt($part);if(!empty($r['staged']))$verified++;if(!empty($r['checkpoint_ready']))$ready=true;}
        foreach(['status','fresh_status','generate','training']as$key){$j=(array)($s[$key]??[]);$d=self::receipt($j);$results[$key]=['job_id'=>$j['id']??null,'state'=>$d===null?'waiting_for_verified_result':'completed_verified','result'=>$d];}
        return ['model'=>'MSW-DualMemory-byte-v1','mode'=>'own_research_model_with_mandatory_paid_GPT_business_reasoning',
            'research_only'=>true,'business_decision_authorized'=>false,'training_total_step_limit'=>null,'steps_per_training_job'=>32,
            'checkpoint_parts'=>['queued'=>count((array)($s['parts']??[])),'verified'=>$verified,'total'=>count(self::manifest()['parts']),'ready'=>$ready],
            'training_cadence'=>'one_continuation_per_UTC_hour_when_previous_receipt_verified','results'=>$results,
            'quality_improvement_not_implied_by_weight_change'=>true];
    }
}
