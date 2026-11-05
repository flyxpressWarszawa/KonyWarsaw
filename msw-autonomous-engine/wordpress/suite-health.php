<?php
/** Shared, request-only operational diagnostics for the MSW plugin suite. */
defined('ABSPATH') || exit;
if (!class_exists('MSW_Suite_Health_V1', false)) {
final class MSW_Suite_Health_V1 {
    public const VERSION = '1.0.0';
    private static array $modules = [];
    private static bool $booted = false;

    public static function register(string $file, array $contract = []): void {
        $id = plugin_basename($file);
        if (!preg_match('~\A[a-z0-9_-]+/[a-zA-Z0-9_.-]+\.php\z~D', $id)) return;
        self::$modules[$id] = $contract;
    }
    public static function allowed(): bool { return current_user_can('manage_options'); }
    public static function boot(): void {
        if (self::$booted) return;
        self::$booted = true;
        add_action('admin_menu', static function (): void {
            add_management_page('MSW — stan modułów','MSW — stan modułów','manage_options','msw-suite-health',[self::class,'page']);
        });
        add_action('rest_api_init', static function (): void {
            register_rest_route('msw-suite/v1', '/status', ['methods'=>'GET', 'permission_callback'=>[self::class,'allowed'],
                'callback'=>static fn()=>self::response(self::snapshot())]);
            register_rest_route('msw-suite/v1', '/analyze', ['methods'=>'POST', 'permission_callback'=>[self::class,'allowed'],
                'callback'=>[self::class,'enqueue']]);
            register_rest_route('msw-suite/v1', '/analysis/(?P<id>[a-f0-9-]{36})', ['methods'=>'GET', 'permission_callback'=>[self::class,'allowed'],
                'callback'=>[self::class,'analysis']]);
            register_rest_route('msw-suite/v1', '/upgrade-receipt', ['methods'=>'GET', 'permission_callback'=>[self::class,'allowed'],
                'callback'=>static fn()=>self::response((array)get_option('msw_suite_upgrade_20261105', []))]);
        });
    }
    private static function response(array $value): WP_REST_Response {
        return new WP_REST_Response($value, 200, ['Cache-Control'=>'no-store, private', 'X-Content-Type-Options'=>'nosniff']);
    }
    public static function page(): void {
        if (!self::allowed()) return;
        $snapshot = self::snapshot();
        echo '<div class="wrap"><h1>MSW — stan modułów</h1><p>Aktualne wersje, zależności i terminy istniejących zadań. Analiza na Renderze nie uruchamia produkcji ani płatnych zapytań AI.</p><table class="widefat striped"><thead><tr><th>Moduł</th><th>Wersja</th><th>Stan</th><th>Brakujące zależności</th><th>Zaplanowane przebiegi</th></tr></thead><tbody>';
        foreach ($snapshot['modules'] as $row) {
            $times = [];
            foreach ($row['cron'] as $cron) $times[]=$cron['hook'].': '.($cron['next_at']?gmdate('Y-m-d H:i:s',$cron['next_at']).' UTC':'brak terminu');
            echo '<tr><td>'.esc_html($row['plugin']).'</td><td>'.esc_html((string)$row['version']).'</td><td>'.esc_html($row['active']?'Aktywny':'Nieaktywny').'</td><td>'.esc_html(implode(', ',$row['missing_dependencies'])?:'—').'</td><td>'.esc_html(implode('; ',$times)?:'—').'</td></tr>';
        }
        echo '</tbody></table>';
        if (class_exists('MSW\\UnifiedEngine\\RemoteStore',false)) {
            echo '<p><button class="button button-primary" type="button" id="msw-suite-analyze">Analizuj na Renderze</button></p><p id="msw-suite-notice" role="status" aria-live="polite"></p><pre id="msw-suite-result" style="white-space:pre-wrap"></pre>';
            $config = wp_json_encode(['url'=>rest_url('msw-suite/v1/'),'nonce'=>wp_create_nonce('wp_rest')],JSON_HEX_TAG|JSON_HEX_AMP|JSON_HEX_APOS|JSON_HEX_QUOT);
            echo '<script>(()=>{"use strict";const c='.$config.';const b=document.getElementById("msw-suite-analyze"),n=document.getElementById("msw-suite-notice"),r=document.getElementById("msw-suite-result");b.addEventListener("click",async()=>{b.disabled=true;try{n.textContent="Zapisuję zadanie analizy…";const response=await fetch(c.url+"analyze",{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json","X-WP-Nonce":c.nonce},body:JSON.stringify({request_id:"admin-"+(crypto.randomUUID?crypto.randomUUID():Date.now()+"-"+Math.random().toString(16).slice(2))})});const job=await response.json();if(!response.ok||!job.job_id)throw new Error("Nie zapisano zadania.");for(let i=0;i<40;i++){n.textContent="Render przetwarza analizę. Pozostałe moduły działają niezależnie.";await new Promise(resolve=>setTimeout(resolve,3000));const check=await fetch(c.url+"analysis/"+job.job_id,{credentials:"same-origin",headers:{"X-WP-Nonce":c.nonce},cache:"no-store"});const result=await check.json();if(!check.ok)throw new Error("Nie odczytano wyniku.");if(result.state==="completed"&&result.result_integrity_verified){n.textContent="Wynik odebrany; suma kontrolna potwierdzona.";r.textContent=JSON.stringify(result.analysis,null,2);return;}if(result.state==="failed")throw new Error("Analiza zapisała błąd; sprawdź diagnostykę centrali.");}n.textContent="Zadanie zapisane. Wynik nie został jeszcze potwierdzony.";}catch(error){n.textContent=error.message;}finally{b.disabled=false;}});})();</script>';
        }
        echo '</div>';
    }
    public static function summarize_state($value): array {
        if (!is_array($value)) return [];
        $out = [];
        if (array_key_exists('enabled', $value)) $out['enabled'] = (bool)$value['enabled'];
        $out['error_present'] = !empty($value['last_error']) || !empty($value['runtime_blocked']);
        if (is_array($value['jobs'] ?? null)) {
            $counts = [];
            foreach (array_slice($value['jobs'], 0, 2000, true) as $job) {
                if (!is_array($job)) continue;
                $state = substr(sanitize_key((string)($job['state'] ?? $job['stage'] ?? 'unknown')), 0, 60);
                $counts[$state] = ($counts[$state] ?? 0) + 1;
            }
            $out['jobs_sampled'] = min(2000, count($value['jobs']));
            $out['job_state_counts'] = $counts;
            $out['jobs_total'] = count($value['jobs']);
        }
        return $out;
    }
    public static function snapshot(): array {
        if (!function_exists('get_plugins')) require_once ABSPATH.'wp-admin/includes/plugin.php';
        global $wp_version;
        $plugins = get_plugins();
        $active = array_fill_keys((array)get_option('active_plugins', []), true);
        if (is_multisite()) $active += (array)get_site_option('active_sitewide_plugins', []);
        $slugs = [];
        foreach ($plugins as $file=>$data) {
            if (isset($active[$file])) $slugs[dirname($file)] = true;
        }
        $rows = [];
        $discovered = self::discover_runtime_contracts();
        foreach (array_slice(self::$modules, 0, 200, true) as $file=>$contract) {
            $auto = $discovered[$file] ?? [];
            $contract['hooks'] = array_values(array_unique(array_merge((array)($contract['hooks']??[]),(array)($auto['hooks']??[]))));
            $contract['options'] = array_values(array_unique(array_merge((array)($contract['options']??[]),(array)($auto['options']??[]))));
            $data = $plugins[$file] ?? null;
            $requires = is_array($data) ? array_filter(array_map('trim', explode(',', (string)($data['RequiresPlugins'] ?? '')))) : [];
            $missing = array_values(array_filter($requires, static fn($slug)=>!isset($slugs[$slug])));
            $row = ['plugin'=>$file, 'installed'=>is_array($data), 'active'=>isset($active[$file]),
                'version'=>is_array($data)?(string)$data['Version']:null, 'contract_version'=>self::VERSION,
                'missing_dependencies'=>$missing, 'php_compatible'=>!is_array($data)||empty($data['RequiresPHP'])||version_compare(PHP_VERSION,$data['RequiresPHP'],'>='),
                'wordpress_compatible'=>!is_array($data)||empty($data['RequiresWP'])||version_compare($wp_version,$data['RequiresWP'],'>='),
                'cron'=>[], 'state'=>[], 'lock_age_seconds'=>null];
            $row['state_evidence_available'] = !empty($contract['options']);
            foreach (array_slice((array)($contract['hooks'] ?? []),0,8) as $hook) {
                if (!is_string($hook) || !preg_match('/\A[a-zA-Z0-9_:-]{1,120}\z/D',$hook)) continue;
                $at = wp_next_scheduled($hook);
                $row['cron'][] = ['hook'=>$hook,'next_at'=>$at?(int)$at:null];
            }
            foreach (array_slice((array)($contract['options'] ?? []),0,3) as $key) {
                if (is_string($key) && preg_match('/\Amsw[a-zA-Z0-9_:-]{1,100}\z/D',$key)) {
                    $row['state'] = array_merge($row['state'], self::summarize_state(get_option($key,[])));
                }
            }
            $key = $contract['lock'] ?? null;
            if (is_string($key) && preg_match('/\Amsw[a-zA-Z0-9_:-]{1,100}\z/D',$key)) {
                $lock = get_option($key, false);
                $row['lock_present'] = (bool)$lock;
                $stamp = is_array($lock)?($lock['at'] ?? null):null;
                $at = is_numeric($stamp)?(int)$stamp:(is_string($stamp)?strtotime($stamp):false);
                if ($at && $at<=time()) $row['lock_age_seconds'] = time()-$at;
            }
            $rows[] = $row;
        }
        return ['schema'=>'msw.suite.snapshot.v1', 'provenance'=>'authenticated_wordpress_plugin_metadata',
            'origin'=>rtrim(home_url('/'),'/'), 'observed_at'=>time(), 'contract_version'=>self::VERSION,
            'modules'=>$rows, 'execution_verified'=>false, 'publication_verified'=>false,
            'new_cron'=>false, 'outbound_requests'=>false, 'heavy_compute_in_wordpress'=>false];
    }
    private static function owner(string $path): ?string {
        $base = str_replace('\\','/',rtrim(WP_PLUGIN_DIR,'/')).'/';
        $path = str_replace('\\','/',$path);
        if (!str_starts_with($path,$base)) return null;
        $parts = explode('/',substr($path,strlen($base)));
        $slug = $parts[0];
        if ($slug==='msw-unified-engine' && ($parts[1]??'')==='modules') $slug=$parts[2]??$slug;
        foreach (self::$modules as $file=>$unused) if (dirname($file)===$slug) return $file;
        return null;
    }
    private static function discover_runtime_contracts(): array {
        $out=[];
        foreach (array_slice(get_declared_classes(),0,5000) as $name) {
            try {
                $class=new ReflectionClass($name); $path=$class->getFileName();
                $owner=is_string($path)?self::owner($path):null;
                if (!$owner) continue;
                foreach (['STATE','STATE_OPTION'] as $key) {
                    $constant=$class->getReflectionConstant($key);
                    if (!$constant || !$constant->isPublic()) continue;
                    $value=$constant->getValue();
                    if (is_string($value) && preg_match('/\Amsw[a-zA-Z0-9_:-]{1,100}\z/D',$value)) $out[$owner]['options'][]=$value;
                }
            } catch (ReflectionException $ignored) {}
        }
        global $wp_filter;
        $cron=function_exists('_get_cron_array')?_get_cron_array():[]; $seen=[]; $inspected=0;
        foreach (array_slice((array)$cron,0,1000,true) as $stamp=>$events) {
            foreach ((array)$events as $hook=>$jobs) {
                if (isset($seen[$hook]) || !isset($wp_filter[$hook]->callbacks)) continue;
                $seen[$hook]=true;
                foreach ($wp_filter[$hook]->callbacks as $priority=>$callbacks) foreach ($callbacks as $callback) {
                    if (++$inspected>1000) return $out;
                    try {
                        $callable=$callback['function'];
                        if (is_array($callable)) $reflection=new ReflectionMethod($callable[0],$callable[1]);
                        elseif ($callable instanceof Closure || (is_string($callable)&&function_exists($callable))) $reflection=new ReflectionFunction($callable);
                        elseif (is_string($callable)&&str_contains($callable,'::')) $reflection=new ReflectionMethod(...explode('::',$callable,2));
                        else continue;
                        $path=$reflection->getFileName();$owner=is_string($path)?self::owner($path):null;
                        if ($owner) $out[$owner]['hooks'][]=$hook;
                    } catch (ReflectionException $ignored) {}
                }
            }
        }
        return $out;
    }
    public static function enqueue(WP_REST_Request $request) {
        $id = $request->get_param('request_id');
        if (!is_string($id) || !preg_match('/\A[a-zA-Z0-9_-]{8,64}\z/D',$id))
            return new WP_Error('suite_request_id','Wymagany identyfikator żądania.', ['status'=>422]);
        if (!class_exists('MSW\\UnifiedEngine\\RemoteStore',false))
            return new WP_Error('suite_worker_bridge_missing','Ten portal korzysta z analizy przez centralę.', ['status'=>409]);
        $key = 'suite-health:'.$id;
        $class = 'MSW\\UnifiedEngine\\RemoteStore';
        $payload = $class::existingPayload('runtime.catalog',$key);
        if ($payload === null) $payload = ['suite_snapshot'=>self::snapshot()];
        $result = $class::enqueue('runtime.catalog',$payload,$key);
        return is_wp_error($result)?$result:self::response($result);
    }
    public static function analysis(WP_REST_Request $request) {
        if (!class_exists('MSW\\UnifiedEngine\\RemoteStore',false))
            return new WP_Error('suite_worker_bridge_missing','Odczyt wyniku odbywa się w centrali.', ['status'=>409]);
        global $wpdb;
        $class = 'MSW\\UnifiedEngine\\RemoteStore';
        $row = $wpdb->get_row($wpdb->prepare('SELECT id,kind,payload,state,result,result_sha256,updated_at FROM '.$class::table().' WHERE id=%s',(string)$request['id']),ARRAY_A);
        $input = $row?json_decode($row['payload'],true):null;
        if (!$row || $row['kind']!=='runtime.catalog' || !isset($input['suite_snapshot']))
            return new WP_Error('suite_job_missing','Nie znaleziono analizy modułów.', ['status'=>404]);
        $out = ['job_id'=>$row['id'],'state'=>$row['state'],'result_integrity_verified'=>false];
        if (in_array($row['state'],['completed','failed'],true)) {
            if (!hash_equals((string)$row['result_sha256'],hash('sha256',(string)$row['result'])))
                return new WP_Error('suite_result_integrity','Nie potwierdzono integralności wyniku.', ['status'=>500]);
            $result = json_decode($row['result'],true);
            $out['result_integrity_verified'] = true;
            $out['result_sha256'] = $row['result_sha256'];
            $out['worker_version'] = $result['data']['worker_version'] ?? null;
            $out['analysis'] = $result['data']['suite'] ?? null;
            $out['error'] = isset($result['error'])?substr(sanitize_key((string)$result['error']),0,80):null;
        }
        return self::response($out);
    }
}
}
MSW_Suite_Health_V1::boot();
