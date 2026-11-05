<?php
/**
 * Plugin Name: MSW AI Wspólny Silnik — WordPress, Render i Python
 * Description: Podpisany most współpracy, wspólna kolejka i potwierdzone wyniki istniejących silników MSW.
 * Version: 1.2.0
 * Requires PHP: 8.1
 * Author: Mobilny Serwis Warszawa
 */
defined('ABSPATH') || exit;

final class MSW_Render_Federation
{
    const SCHEMA = 'msw.federation.v1';

    public static function boot(): void
    {
        add_action('rest_api_init', [self::class, 'routes']);
        add_filter('msw_ai_federation_submit', [self::class, 'submit_filter'], 10, 4);
        add_action('mswue_remote_result', [self::class, 'completed'], 30, 4);
        add_action('msw_central_engine_register_capabilities', [self::class, 'register_capabilities']);
        add_action('mswh_integrations_synced', [self::class, 'catalogue_changed'], 40, 1);
    }

    private static function error(string $code, int $status = 400): WP_Error
    {
        return new WP_Error('mswf_'.$code, 'MSW Federation: '.$code, ['status' => $status]);
    }

    public static function admin(): bool { return current_user_can('manage_options'); }

    public static function register_capabilities(): void
    {
        if(!is_callable(['MSW_Central_Engine_Registry','register']))return;
        MSW_Central_Engine_Registry::register('federation.status',[
            'label'=>'MSW AI — WordPress, Render i serwery Python','plugin'=>'msw-render-federation/msw-render-federation.php','effect'=>'read',
            'input_schema'=>['type'=>'object','additionalProperties'=>false,'properties'=>new stdClass()],
            'permission_callback'=>[self::class,'admin'],'callback'=>static fn()=>self::snapshot()]);
        MSW_Central_Engine_Registry::register('federation.result',[
            'label'=>'MSW AI — odczyt potwierdzonego wyniku współpracy','plugin'=>'msw-render-federation/msw-render-federation.php','effect'=>'read',
            'input_schema'=>['type'=>'object','additionalProperties'=>false,'required'=>['job_id'],'properties'=>['job_id'=>['type'=>'string','pattern'=>'^[a-f0-9-]{36}$']]],
            'permission_callback'=>[self::class,'admin'],'callback'=>static fn($a)=>self::result(['id'=>$a['job_id']])]);
        MSW_Central_Engine_Registry::register('federation.submit',[
            'label'=>'MSW AI — wspólne zadanie rozwoju WordPress, Render i Python','plugin'=>'msw-render-federation/msw-render-federation.php','effect'=>'write',
            'input_schema'=>['type'=>'object','additionalProperties'=>false,'required'=>['prompt'],
                'properties'=>['prompt'=>['type'=>'string','minLength'=>1,'maxLength'=>6000],'task'=>['type'=>'string','enum'=>['technical','business','seo','article','review','voice']],
                    'max_tokens'=>['type'=>'integer','minimum'=>64,'maximum'=>3000],'use_knowledge'=>['type'=>'boolean'],'collaborate'=>['type'=>'boolean']]],
            'permission_callback'=>[self::class,'admin'],
            'callback'=>static fn($a,$ctx)=>self::submit(array_merge($a,['request_id'=>'ce-'.hash('sha256',$ctx['request_id'])])),
            'verify'=>static function($input,$result){
                global $wpdb;$table=\MSW\UnifiedEngine\RemoteStore::table();
                $row=$wpdb->get_row($wpdb->prepare("SELECT id,kind,payload_sha256 FROM {$table} WHERE id=%s",$result['job_id']??''),ARRAY_A);
                $ok=is_array($row)&&$row['kind']==='llm.generate'&&hash_equals($row['payload_sha256'],(string)($result['payload_sha256']??''));
                return ['verified'=>$ok,'evidence'=>['job_id'=>$row['id']??null,'queue_readback_verified'=>$ok,'generation_finished'=>false]];
            }]);
    }

    public static function connect()
    {
        if(!class_exists('MSW_Central_Engine_Core'))return self::error('central_engine_missing',503);
        $catalog=MSW_Central_Engine_Registry::catalog();
        if(($catalog['federation.submit']['plugin']??'')!=='msw-render-federation/msw-render-federation.php')return self::error('central_capability_conflict',409);
        $option=MSW_Central_Engine_Core::OPTION;$settings=(array)get_option($option,[]);
        $caps=(array)($settings['write_capabilities']??MSW_Central_Engine_Core::defaults()['write_capabilities']);
        if(!in_array('federation.submit',$caps,true))$caps[]='federation.submit';
        $settings['write_capabilities']=$caps;update_option($option,$settings,false);
        if((array)get_option($option,[])!==$settings)return self::error('central_policy_readback',503);
        update_option('msw_federation_catalogue_auto',true,false);
        if(class_exists('MSWH_Integrations')){
            $feed=MSWH_Integrations::feed();
            if(!is_wp_error($feed)&&($feed['complete']??false)===true)self::catalogue_changed($feed['entries']);
        }
        return ['connected'=>true,'central_capability'=>'federation.submit','policy_readback_verified'=>true,
            'automatic_trigger'=>'existing_public_catalogue_change_event','additional_cron_created'=>false,
            'catalogue_job'=>get_option('msw_federation_catalogue_job',[])];
    }

    public static function catalogue_changed($entries): void
    {
        if(get_option('msw_federation_catalogue_auto',false)!==true||!is_array($entries)||!$entries)return;
        $sources=[];
        foreach(array_slice($entries,0,100) as $e){
            if(!is_array($e)||!is_string($e['url']??null)||!is_string($e['title']??null))continue;
            if(wp_parse_url($e['url'],PHP_URL_HOST)!==wp_parse_url(home_url(),PHP_URL_HOST))continue;
            $sources[]=['url'=>$e['url'],'title'=>$e['title'],'content_sha256'=>hash('sha256',(string)($e['text']??''))];
        }
        if(!$sources)return;
        usort($sources,static fn($a,$b)=>strcmp($a['url'],$b['url']));
        $digest=hash('sha256',wp_json_encode($sources,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES));
        $text='Spawanie, bramy i pozostałe usługi MSW: zaproponuj trzy konkretne kroki rozwoju na podstawie aktualnego publicznego katalogu oraz wspólnej pamięci. Oddziel bieżące potwierdzenia techniczne od historycznych źródeł i propozycji. Nie deklaruj wykonania działań. Katalog: ';
        $text.=function_exists('mb_substr')?mb_substr(implode('; ',array_column($sources,'title')),0,1200):substr(implode('; ',array_column($sources,'title')),0,1200);
        $r=self::submit(['request_id'=>'catalogue-'.$digest,'task'=>'business','prompt'=>$text,'max_tokens'=>1800,'use_knowledge'=>true,'collaborate'=>true]);
        if(!is_wp_error($r))update_option('msw_federation_catalogue_job',['source_sha256'=>$digest,'job_id'=>$r['job_id'],'state'=>$r['state'],'source_count'=>count($sources)],false);
    }

    public static function permission($request)
    {
        if (self::admin()) return true;
        $raw = $request->get_body();
        if (strlen($raw) > 20000 || !class_exists('MSW\\UnifiedEngine\\RemoteStore')) return self::error('unavailable', 503);
        $body = json_decode($raw, true);
        if (!is_array($body) || ($body['schema'] ?? '') !== self::SCHEMA) return self::error('schema', 403);
        $headers = [];
        foreach (['node','timestamp','nonce','signature'] as $field) $headers[$field] = $request->get_header('x-mswue-'.$field);
        $node = \MSW\UnifiedEngine\RemoteStore::authorise('status', $raw, $headers);
        if (is_wp_error($node)) return $node;
        $config = \MSW\UnifiedEngine\RemoteStore::config();
        $kind = in_array($body['operation'] ?? '', ['generate','python_advice','local_advice','owned_advice'], true) ? 'llm.generate' : 'runtime.catalog';
        if (!in_array($kind, $config['nodes'][$node]['kinds'] ?? [], true)) return self::error('scope', 403);
        return true;
    }

    public static function routes(): void
    {
        register_rest_route('msw-federation/v1', '/gateway', ['methods' => 'POST', 'permission_callback' => [self::class, 'permission'],
            'callback' => [self::class, 'gateway']]);
        register_rest_route('msw-federation/v1', '/submit', ['methods' => 'POST', 'permission_callback' => [self::class, 'admin'],
            'callback' => static fn($r) => self::submit((array)$r->get_json_params())]);
        register_rest_route('msw-federation/v1', '/connect', ['methods'=>'POST','permission_callback'=>[self::class,'admin'],'callback'=>[self::class,'connect']]);
        register_rest_route('msw-federation/v1', '/result/(?P<id>[a-f0-9-]{36})', ['methods' => 'GET', 'permission_callback' => [self::class, 'admin'],
            'callback' => [self::class, 'result']]);
        register_rest_route('msw-federation/v1', '/status', ['methods' => 'GET', 'permission_callback' => [self::class, 'admin'],
            'callback' => static fn() => ['schema' => self::SCHEMA, 'version' => '1.2.0', 'queue_available' => class_exists('MSW\\UnifiedEngine\\RemoteStore'),
                'router_loaded' => class_exists('MSW_ChatGPT_Router'), 'python_adapter_loaded' => class_exists('MSWH_Engine_Adapters'),
                'last_result' => get_option('msw_federation_last_result', []), 'all_plugin_callbacks_automatically_migrated' => false]]);
    }

    public static function snapshot(): array
    {
        if (!function_exists('get_plugins')) require_once ABSPATH.'wp-admin/includes/plugin.php';
        $plugins = get_plugins();
        $active = get_option('active_plugins', []);
        $inventory = [];
        foreach ($plugins as $file => $data) $inventory[] = ['id' => $file, 'name' => $data['Name'], 'version' => $data['Version'], 'active' => in_array($file, $active, true)];
        $hub = ['reachable' => false, 'model_inference_verified' => false, 'providers' => []];
        if (function_exists('mswh_call')) {
            $health = mswh_call('/v1/admin/health', [], true, 5);
            if (!is_wp_error($health) && ($health['ok'] ?? false) === true) {
                $hub['reachable'] = true;
                $hub['version'] = sanitize_text_field((string)($health['version'] ?? ''));
                foreach (array_slice((array)($health['providers'] ?? []), 0, 30) as $provider) {
                    if (is_array($provider)) $hub['providers'][] = ['id' => sanitize_key((string)($provider['id'] ?? '')), 'state' => sanitize_key((string)($provider['state'] ?? ''))];
                }
            } else $hub['error'] = is_wp_error($health) ? $health->get_error_code() : 'health_unverified';
        } else $hub['error'] = 'hub_bridge_not_loaded';
        return ['origin' => home_url(), 'observed_at' => time(),
            'wordpress' => ['installed' => count($plugins), 'active' => count(array_intersect(array_keys($plugins), $active)), 'catalogued' => count($inventory)],
            'plugin_inventory' => $inventory, 'python_hub' => $hub, 'server_applications' => self::server_inventory(), 'local_model' => self::local_health(),
            'server_engine_summary' => class_exists('MSWH_Engine_Adapters') ? MSWH_Engine_Adapters::summary() : 'not_available',
            'inventory_is_not_execution_proof' => true];
    }

    public static function node_signature(string $key, string $node, string $method, string $path, string $stamp, string $nonce, string $body): string
    {
        return hash_hmac('sha256', implode("\n", ['msw.node.hmac.v1',$node,$method,$path,$stamp,$nonce,hash('sha256',$body)]), $key);
    }

    public static function node_response_valid(string $key, string $node, string $nonce, int $status, string $raw, string $digest, string $signature): bool
    {
        $hash=hash('sha256',$raw);
        $expected=hash_hmac('sha256',implode("\n",['msw.node.response.v1',$node,$nonce,(string)$status,$hash]),$key);
        return hash_equals($hash,$digest) && hash_equals($expected,$signature);
    }

    private static function local_call(string $path, ?array $payload = null)
    {
        $private='/home/vy9w5irftdq0/msw-llm-private/';
        foreach(['node.json','node.key'] as $name){
            $file=$private.$name;
            if(!is_file($file)||is_link($file)||!is_readable($file)||(fileperms($file)&0077)!==0||filesize($file)>16384)return self::error('local_private_config',503);
        }
        $config=json_decode((string)file_get_contents($private.'node.json'),true);
        $hex=trim((string)file_get_contents($private.'node.key'));
        $host=(string)wp_parse_url(home_url(),PHP_URL_HOST);
        if(!is_array($config)||!in_array($host,$config['allowed_sites']??[],true)||!preg_match('/^[a-z0-9][a-z0-9-]{2,47}$/D',$config['node_id']??'')||!preg_match('/^[a-f0-9]{64}$/D',$hex))return self::error('local_pairing',503);
        $key=hex2bin($hex);$node=$config['node_id'];$method=$payload===null?'GET':'POST';
        $body=$payload===null?'':wp_json_encode($payload,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES);
        $stamp=(string)time();$nonce=bin2hex(random_bytes(16));
        $headers=['Content-Type'=>'application/json','X-MSW-Node'=>$node,'X-MSW-Timestamp'=>$stamp,'X-MSW-Nonce'=>$nonce,
            'X-MSW-Signature'=>self::node_signature($key,$node,$method,$path,$stamp,$nonce,$body)];
        $args=['method'=>$method,'headers'=>$headers,'timeout'=>$payload===null?8:50,'redirection'=>0,'sslverify'=>true,'reject_unsafe_urls'=>true,'limit_response_size'=>16384];
        if($payload!==null)$args['body']=$body;
        $r=wp_safe_remote_request(rtrim(home_url(),'/').'/msw-llm-node'.$path,$args);
        if(is_wp_error($r))return self::error('local_transport',503);
        $status=(int)wp_remote_retrieve_response_code($r);$raw=(string)wp_remote_retrieve_body($r);
        if(!self::node_response_valid($key,$node,$nonce,$status,$raw,(string)wp_remote_retrieve_header($r,'x-msw-response-sha256'),(string)wp_remote_retrieve_header($r,'x-msw-response-signature')))return self::error('local_response_signature',502);
        $data=json_decode($raw,true);
        if($status!==200||!is_array($data))return self::error('local_http_'.$status,503);
        return $data;
    }

    public static function local_health(): array
    {
        $h=self::local_call('/health');
        if(is_wp_error($h))return ['reachable'=>false,'generation_ack'=>false,'error'=>$h->get_error_code()];
        return array_intersect_key($h,array_flip(['schema','node_id','model','expected_weight_sha256','weights_present','weights_loaded','runtime_expected','runtime_mode','status','last_attempt','limits','training_performed']));
    }

    public static function local_advice(string $question): array
    {
        $health=self::local_health();
        if(empty($health['weights_present']))return ['state'=>'model_not_ready','health'=>$health,'generation_ack'=>false];
        $text=preg_replace('/[\x00-\x1f\x7f]/u',' ',wp_strip_all_tags($question));
        $metadata=['title'=>'MSW AI — doradztwo techniczne','description'=>function_exists('mb_substr')?mb_substr($text,0,220):substr($text,0,220),'page_kind'=>'service','language'=>'pl'];
        $id='federated-'.substr(hash('sha256',wp_json_encode($metadata)),0,40);
        $payload=['schema'=>'msw.node.metadata-advisory.request.v1','request_id'=>$id,'site_id'=>(string)wp_parse_url(home_url(),PHP_URL_HOST),'metadata'=>$metadata];
        $r=self::local_call('/v1/metadata-advisory',$payload);
        if(is_wp_error($r))return ['state'=>'generation_unavailable','generation_ack'=>false,'error'=>$r->get_error_code()];
        $valid=($r['schema']??'')==='msw.node.metadata-advisory.result.v1'&&($r['request_id']??'')===$id&&($r['generation_ack']??false)===true
            &&($r['request_sha256']??'')===hash('sha256',wp_json_encode($payload,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES))
            &&($r['weight_sha256']??'')==='2e8040ceae7815abe0dcb3540b9995eaa1fa0d2ca9e797d0a635ae4433c68c2d'
            &&($r['runtime']??'')==='llama-cpp-python-0.3.36-cpu'&&($r['weights_loaded_for_generation']??false)===true
            &&is_int($r['tokens_generated']??null)&&$r['tokens_generated']>=1&&$r['tokens_generated']<=32&&is_string($r['text']??null);
        if(!$valid)return ['state'=>'inference_receipt_invalid','generation_ack'=>false];
        return array_intersect_key($r,array_flip(['model','text','request_id','request_sha256','weight_sha256','runtime','generation_ack','tokens_generated','elapsed_ms','weights_loaded_for_generation']))+['state'=>'verified_inference','quality'=>'unreviewed_advisory'];
    }

    public static function owned_advice(string $question): array
    {
        if(!class_exists('MSW_Owned_LLM_Bridge')||empty(MSW_Owned_LLM_Bridge::policy()['enabled']))return ['state'=>'not_enabled'];
        try{
            $prompt='Suggest one technical quality check for this public MSW service metadata: '.substr(preg_replace('/[\x00-\x1f\x7f]/',' ',$question),0,450);
            $r=MSW_Owned_LLM_Bridge::request($prompt,'fed-owned-'.substr(hash('sha256',$prompt),0,40));
            if(is_wp_error($r))return ['state'=>'unavailable','error'=>$r->get_error_code()];
            return array_intersect_key($r,array_flip(['text','model','weight_sha256','runtime','tokens_generated','request_id']))+['state'=>'native_contract_verified','quality'=>'unreviewed_advisory'];
        }catch(Throwable $e){return ['state'=>'pairing_or_transport_unavailable'];}
    }

    /** Fixed owned backend locations: return names/hashes, never source or configuration contents. */
    public static function server_inventory(): array
    {
        // This account hosts WordPress in a domain subdirectory of public_html.
        // Its Python applications also live outside public_html.
        $home = realpath('/home/vy9w5irftdq0');
        if (!$home) return [];
        $roots = [ABSPATH.'msw-backend'];
        $file = ABSPATH.'msw-backend/.htaccess';
        if (is_readable($file)) {
            $configuration = file_get_contents($file, false, null, 0, 65536);
            if (is_string($configuration) && preg_match('/^\s*PassengerAppRoot\s+["\x27]?([^"\x27\r\n]+)/mi', $configuration, $m)) $roots[] = trim($m[1]);
        }
        foreach ((array)glob($home.'/*', GLOB_ONLYDIR) as $candidate) if (preg_match('/msw|python|engine|model|malta|forge|brain/i', basename($candidate))) $roots[] = $candidate;
        $out = [];
        foreach (array_slice(array_unique($roots), 0, 12) as $candidate) {
            $root = realpath($candidate);
            if (!$root || !is_dir($root) || !str_starts_with($root.'/', $home.'/')) continue;
            $files = [];
            foreach (array_slice(array_merge((array)glob($root.'/*.py'), (array)glob($root.'/*/*.py')), 0, 6) as $source) {
                if (!is_file($source) || is_link($source) || !is_readable($source) || filesize($source) > 500000) continue;
                $resolved = realpath($source);
                if (!$resolved || !str_starts_with($resolved, $root.'/')) continue;
                $code = file_get_contents($source);
                preg_match_all('/^\s*(?:async\s+)?def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(/m', $code, $functions);
                $files[] = ['file' => substr(substr($source, strlen($root)+1),0,256), 'sha256' => hash('sha256', $code), 'functions' => array_map(static fn($n) => substr($n,0,50), array_slice($functions[1], 0, 20))];
            }
            $out[] = ['directory' => substr($root, strlen($home)+1), 'python_files' => $files, 'running_verified' => false];
        }
        return $out;
    }

    public static function python_advice(string $question): array
    {
        if (!function_exists('mswh_call') || !class_exists('MSWH_Engine_Adapters')) return ['state' => 'adapter_unavailable'];
        $query = MSWH_Engine_Adapters::service_query($question);
        if ($query === '') return ['state' => 'no_service_topic'];
        $search = mswh_call('/v1/search', ['query' => $query], false, 5);
        if (is_wp_error($search)) return ['state' => 'search_unavailable', 'error' => $search->get_error_code()];
        foreach (array_slice((array)($search['results'] ?? []), 0, 3) as $row) {
            if (!is_array($row)) continue;
            $id = $row['service_id'] ?? ($row['id'] ?? null);
            if (!is_string($id) || !preg_match('/^[a-zA-Z0-9_.:-]{1,120}$/D', $id)) continue;
            // Existing native offer operation; no case data and no WordPress publication.
            $draft = mswh_call('/v1/admin/draft', ['service_id' => $id, 'case_id' => null], true, 25);
            if (is_wp_error($draft)) return ['state' => 'native_advice_unavailable', 'error' => $draft->get_error_code()];
            $text = isset($draft['content']) && is_string($draft['content']) ? trim(wp_strip_all_tags($draft['content'])) : '';
            if ($text === '') return ['state' => 'native_text_empty'];
            return ['state' => 'native_text_received', 'text' => function_exists('mb_substr') ? mb_substr($text,0,2000) : substr($text,0,2000),
                'service_id' => $id, 'model_inference_verified' => false, 'publication_performed' => false];
        }
        return ['state' => 'no_native_service_id'];
    }

    public static function gateway($request)
    {
        $p = (array)$request->get_json_params();
        $operation = $p['operation'] ?? '';
        $allowed = ['snapshot' => ['schema','operation'], 'local_advice'=>['schema','operation','query'], 'owned_advice'=>['schema','operation','query'], 'public_context' => ['schema','operation','query'], 'python_advice' => ['schema','operation','query'], 'generate' => ['schema','operation','task','text','max_tokens']];
        if (($p['schema'] ?? '') !== self::SCHEMA || !is_string($operation) || !isset($allowed[$operation]) || array_diff(array_keys($p), $allowed[$operation])) return self::error('arguments');
        if ($operation === 'snapshot') $data = self::snapshot();
        elseif(in_array($operation,['local_advice','owned_advice'],true)){
            if(!is_string($p['query']??null)||strlen($p['query'])>6000)return self::error('query');
            $data=self::{$operation}($p['query']);
        }
        elseif ($operation === 'python_advice') {
            if (!is_string($p['query'] ?? null) || strlen($p['query']) > 6000) return self::error('query');
            $data = self::python_advice($p['query']);
        }
        elseif ($operation === 'public_context') {
            if (!is_string($p['query'] ?? null) || strlen($p['query']) > 6000) return self::error('query');
            $items = class_exists('MSWH_Engine_Adapters') ? MSWH_Engine_Adapters::lookup($p['query'], 'render_federation') : [];
            $data = ['items' => array_slice($items, 0, 3), 'service_query'=>class_exists('MSWH_Engine_Adapters')?MSWH_Engine_Adapters::service_query($p['query']):'', 'scope' => 'live_exported_public_service_sources', 'private_cases_exported' => false];
        } else {
            if (!class_exists('MSW_ChatGPT_Router') || !is_string($p['text'] ?? null) || strlen($p['text']) > 10000
                || !in_array($p['task'] ?? '', ['technical','business','seo','article','review','voice'], true)
                || !is_int($p['max_tokens'] ?? null) || $p['max_tokens'] < 64 || $p['max_tokens'] > 3000) return self::error('model_arguments');
            $answer = MSW_ChatGPT_Router::generate($p['task'], $p['text'], ['max_output_tokens' => $p['max_tokens']]);
            if (is_wp_error($answer)) return self::error('model_'.$answer->get_error_code(), 502);
            $model = defined('MSW_CHATGPT_MODEL') ? (string)MSW_CHATGPT_MODEL : 'gpt-5-mini';
            $data = ['text' => $answer, 'model' => $model, 'provider' => 'openai_existing_router'];
        }
        return new WP_REST_Response(['schema' => self::SCHEMA, 'ok' => true, 'data' => $data], 200, ['Cache-Control' => 'no-store, private']);
    }

    public static function submit(array $p)
    {
        if (!class_exists('MSW\\UnifiedEngine\\RemoteStore') || array_diff(array_keys($p), ['prompt','task','max_tokens','use_knowledge','collaborate','request_id'])) return self::error('submit_arguments');
        if (!is_string($p['prompt'] ?? null) || !strlen(trim($p['prompt'])) || strlen($p['prompt']) > 6000) return self::error('prompt');
        $key = $p['request_id'] ?? '';
        if (!is_string($key) || !preg_match('/^[a-zA-Z0-9_.:-]{8,120}$/D', $key)) return self::error('request_id');
        unset($p['request_id']);
        return \MSW\UnifiedEngine\RemoteStore::enqueue('llm.generate', $p, 'federation:'.$key);
    }

    public static function submit_filter($previous, $task, $text, $context = [])
    {
        if ($previous !== null) return $previous;
        return self::submit(array_merge(array_intersect_key((array)$context, array_flip(['request_id','max_tokens','use_knowledge','collaborate'])), ['task' => $task, 'prompt' => $text]));
    }

    public static function result($request)
    {
        global $wpdb;
        $table = \MSW\UnifiedEngine\RemoteStore::table();
        $row = $wpdb->get_row($wpdb->prepare("SELECT id,kind,state,result,result_sha256 FROM {$table} WHERE id=%s", (string)$request['id']), ARRAY_A);
        if (!$row) return self::error('missing', 404);
        $verified = $row['result'] !== '' && hash_equals($row['result_sha256'], hash('sha256', $row['result']));
        return ['job_id' => $row['id'], 'state' => $row['state'], 'result_sha256' => $row['result_sha256'], 'digest_verified' => $verified,
            'result' => $verified ? json_decode($row['result'], true) : null];
    }

    public static function completed($kind, $id, $payload, $result): void
    {
        if ($kind !== 'llm.generate' || ($result['data']['schema'] ?? '') !== 'msw.federation.result.v1') return;
        update_option('msw_federation_last_result', ['job_id' => $id, 'at' => time(), 'task' => $result['data']['task'],
            'answer_sha256' => $result['data']['answer_sha256'], 'model' => $result['data']['model'], 'contributors' => $result['data']['contributors']], false);
        do_action('msw_ai_federation_result', $id, $result['data']);
    }
}
MSW_Render_Federation::boot();
