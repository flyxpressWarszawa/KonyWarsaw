<?php
/**
 * Plugin Name: MSW Hermes cPanel Broker
 * Description: Właścicielski transfer połączeń i podpisane odczyty pięciu kont dla Hermesa.
 * Version: 0.1.0
 * Requires PHP: 8.1
 */
declare(strict_types=1);
if (!defined('ABSPATH')) exit;

const MSW_HCB_HOSTS = [
    'sxb1plzcpnl507797.prod.sxb1.secureserver.net' => 'cpanel.naprawajacuzziwarszawa.pl',
    'sxb1plzcpnl508174.prod.sxb1.secureserver.net' => 'cpanel.elektryk-warszawa24.pl',
    'sxb1plzcpnl508218.prod.sxb1.secureserver.net' => 'cpanel.e-deweloperski.pl',
    'sxb1plzcpnl508929.prod.sxb1.secureserver.net' => 'cpanel.serwismaszynfitness.pl',
    'sxb1plzcpnl505736.prod.sxb1.secureserver.net' => 'cpanel.e-hydraulikwarszawa.pl',
];

function msw_hcb_path(): string {
    $root = realpath(ABSPATH);
    if (!$root || !preg_match('~^(/home/[^/]+)/public_html/mobilny-serwis-warszawa\.pl/?$~', rtrim($root, '/'), $match)) throw new RuntimeException('unexpected_wordpress_root');
    $dir = $match[1] . '/.msw-hermes-cpanel';
    if (is_link($dir) || (!is_dir($dir) && !mkdir($dir, 0700)) || !is_dir($dir)) throw new RuntimeException('private_store_unavailable');
    chmod($dir, 0700);
    return $dir;
}
function msw_hcb_read(string $name): array {
    $path = msw_hcb_path() . '/' . $name . '.json';
    if (!is_file($path) || is_link($path)) return [];
    $raw = file_get_contents($path);
    $value = is_string($raw) ? json_decode($raw, true, 20) : null;
    return is_array($value) ? $value : [];
}
function msw_hcb_write(string $name, array $value): void {
    $path = msw_hcb_path() . '/' . $name . '.json';
    if (is_link($path)) throw new RuntimeException('symlink_rejected');
    $tmp = tempnam(msw_hcb_path(), 'hcb-');
    if (!$tmp) throw new RuntimeException('private_write_failed');
    try {
        chmod($tmp, 0600);
        if (file_put_contents($tmp, wp_json_encode($value, JSON_THROW_ON_ERROR), LOCK_EX) === false || !rename($tmp, $path)) throw new RuntimeException('private_write_failed');
        chmod($path, 0600);
    } finally { if (is_file($tmp)) unlink($tmp); }
}
function msw_hcb_keys(): array {
    $keys = msw_hcb_read('keys');
    if (!empty($keys['private']) && !empty($keys['aes'])) return $keys;
    $pair = openssl_pkey_new(['private_key_type'=>OPENSSL_KEYTYPE_RSA, 'private_key_bits'=>3072]);
    if (!$pair || !openssl_pkey_export($pair, $private)) throw new RuntimeException('rsa_unavailable');
    $details = openssl_pkey_get_details($pair);
    if (!$details || empty($details['key'])) throw new RuntimeException('rsa_unavailable');
    $keys = ['private'=>$private, 'public'=>$details['key'], 'aes'=>base64_encode(random_bytes(32))];
    msw_hcb_write('keys', $keys);
    return $keys;
}
function msw_hcb_unwrap(string $encoded, string $private): string {
    $cipher = base64_decode($encoded, true);
    if (!$cipher || strlen($cipher)>512 || !openssl_private_decrypt($cipher, $plain, $private, OPENSSL_PKCS1_OAEP_PADDING)) throw new RuntimeException('encrypted_field_invalid');
    return $plain;
}
function msw_hcb_seal(string $secret, string $key): array {
    $iv = random_bytes(12); $tag = '';
    $cipher = openssl_encrypt($secret, 'aes-256-gcm', $key, OPENSSL_RAW_DATA, $iv, $tag);
    if ($cipher === false) throw new RuntimeException('seal_failed');
    return ['iv'=>base64_encode($iv), 'tag'=>base64_encode($tag), 'cipher'=>base64_encode($cipher)];
}
function msw_hcb_unseal(array $value, string $key): string {
    foreach (['iv','tag','cipher'] as $field) if (!is_string($value[$field]??null)) throw new RuntimeException('vault_invalid');
    $plain = openssl_decrypt(base64_decode($value['cipher'],true), 'aes-256-gcm', $key, OPENSSL_RAW_DATA, base64_decode($value['iv'],true), base64_decode($value['tag'],true));
    if ($plain === false) throw new RuntimeException('vault_invalid');
    return $plain;
}
function msw_hcb_domains(string $host, string $user, string $token): array {
    $gateway = MSW_HCB_HOSTS[$host] ?? null;
    if (!$gateway) throw new RuntimeException('host_not_allowed');
    $ch = curl_init('https://' . $gateway . '/execute/DomainInfo/list_domains');
    if (!$ch) throw new RuntimeException('transport_unavailable');
    curl_setopt_array($ch, [CURLOPT_RETURNTRANSFER=>true, CURLOPT_CONNECTTIMEOUT=>4, CURLOPT_TIMEOUT=>12,
        CURLOPT_FOLLOWLOCATION=>false, CURLOPT_SSL_VERIFYPEER=>true, CURLOPT_SSL_VERIFYHOST=>2,
        CURLOPT_HTTPHEADER=>['Authorization: cpanel ' . $user . ':' . $token,'Accept: application/json'],
        CURLOPT_MAXREDIRS=>0, CURLOPT_PROTOCOLS=>CURLPROTO_HTTPS]);
    try { $raw = curl_exec($ch); $code = curl_getinfo($ch, CURLINFO_RESPONSE_CODE); }
    finally { curl_close($ch); }
    if ($code!==200 || !is_string($raw) || strlen($raw)>262144) throw new RuntimeException('cpanel_unavailable');
    $body = json_decode($raw, true, 20);
    if (!is_array($body) || (int)($body['status']??$body['result']['status']??0)!==1) throw new RuntimeException('cpanel_rejected');
    $data = $body['data']??$body['result']['data']??null;
    $main = is_array($data) ? ($data['main_domain']??$data['main']??null) : null;
    if (!is_string($main) || !preg_match('/^[a-z0-9.-]+$/D',$main)) throw new RuntimeException('identity_unverified');
    return ['main_domain'=>$main];
}
function msw_hcb_import(WP_REST_Request $request): WP_REST_Response|WP_Error {
    try {
        if (strlen($request->get_body())>8192 || get_transient('msw_hcb_import_rate')) throw new RuntimeException('import_rate_limited');
        set_transient('msw_hcb_import_rate',1,30);
        $body=$request->get_json_params();
        if (!is_array($body) || ($body['schema']??'')!=='msw.hermes.cpanel.import.v1' || !is_array($body['accounts']??null) || count($body['accounts'])!==5 || !is_string($body['machine_secret']??null)) throw new RuntimeException('import_shape_invalid');
        $keys=msw_hcb_keys(); $seen=[]; $records=[]; $aes=base64_decode($keys['aes'],true);
        $serviceKey=msw_hcb_unwrap($body['machine_secret'],$keys['private']);
        if (!preg_match('/^[a-f0-9]{64}$/D',$serviceKey)) throw new RuntimeException('service_key_invalid');
        foreach ($body['accounts'] as $row) {
            if (!is_array($row)) throw new RuntimeException('account_invalid');
            $host=$row['host']??''; $user=$row['username']??''; $expected=$row['main_domain']??'';
            if (!is_string($host)||!isset(MSW_HCB_HOSTS[$host])||isset($seen[$host])||!is_string($user)||!preg_match('/^[A-Za-z0-9_-]{1,64}$/D',$user)||!is_string($expected)||!preg_match('/^[a-z0-9.-]+$/D',$expected)||!is_string($row['encrypted_token']??null)) throw new RuntimeException('account_invalid');
            $seen[$host]=true;
            $token=msw_hcb_unwrap($row['encrypted_token'],$keys['private']);
            if (!preg_match('/^[A-Za-z0-9_-]{16,512}$/D',$token)) throw new RuntimeException('token_invalid');
            $domain=msw_hcb_domains($host,$user,$token)['main_domain'];
            if (!hash_equals($expected,$domain)) throw new RuntimeException('account_identity_changed');
            $records[$host]=['username'=>$user,'main_domain'=>$domain,'token'=>msw_hcb_seal($token,$aes)];
        }
        if (count($seen)!==count(MSW_HCB_HOSTS)) throw new RuntimeException('account_count_invalid');
        $prior=msw_hcb_read('vault');
        if ($prior && ($prior['accounts']??null)!==$records) throw new RuntimeException('vault_already_initialized');
        msw_hcb_write('vault',['schema'=>'msw.hermes.cpanel.vault.v1','accounts'=>$records,'machine_secret'=>msw_hcb_seal($serviceKey,$aes),'imported_at'=>gmdate('c')]);
        return new WP_REST_Response(['imported'=>true,'verified_accounts'=>array_keys($records),'checked_at'=>gmdate('c')],200);
    } catch (Throwable $e) { return new WP_Error('broker_import_rejected',$e->getMessage(),['status'=>403]); }
}
function msw_hcb_machine_permission(WP_REST_Request $request): bool|WP_Error {
    try {
        $vault=msw_hcb_read('vault'); $keys=msw_hcb_keys();
        if (!$vault || empty($vault['machine_secret'])) throw new RuntimeException('not_paired');
        $time=$request->get_header('x-msw-time'); $nonce=$request->get_header('x-msw-nonce'); $sig=$request->get_header('x-msw-signature');
        if (!preg_match('/^[0-9]{10}$/D',(string)$time) || abs(time()-(int)$time)>60 || !preg_match('/^[a-f0-9-]{36}$/D',(string)$nonce) || !preg_match('/^[a-f0-9]{64}$/D',(string)$sig)) throw new RuntimeException('signature_shape_invalid');
        $secret=msw_hcb_unseal($vault['machine_secret'],base64_decode($keys['aes'],true));
        $payload=$time."\n".$nonce."\n".$request->get_body();
        if (!hash_equals(hash_hmac('sha256',$payload,$secret),$sig)) throw new RuntimeException('signature_invalid');
        $marker='msw_hcb_nonce_'.hash('sha256',$nonce);
        if (!add_option($marker,(string)time(),'','no')) throw new RuntimeException('replay');
        // Old nonces are removed during the signed path; no additional cron.
        if (random_int(1,16)===1) {
            global $wpdb;
            $wpdb->query($wpdb->prepare("DELETE FROM {$wpdb->options} WHERE option_name LIKE %s AND CAST(option_value AS UNSIGNED) < %d",$wpdb->esc_like('msw_hcb_nonce_').'%',time()-300));
        }
        return true;
    } catch (Throwable $e) { return new WP_Error('broker_auth_failed','Signed request required.',['status'=>403]); }
}
function msw_hcb_status(WP_REST_Request $request): WP_REST_Response|WP_Error {
    try {
        $body=$request->get_json_params(); $id=$body['request_id']??null;
        if (!is_string($id)||!preg_match('/^[a-f0-9-]{36}$/D',$id)) throw new RuntimeException('request_id_invalid');
        $vault=msw_hcb_read('vault'); $keys=msw_hcb_keys();
        if (count($vault['accounts']??[])!==5) throw new RuntimeException('vault_incomplete');
        $results=[];
        foreach (MSW_HCB_HOSTS as $host=>$gateway) {
            $row=$vault['accounts'][$host]??null;
            if (!$row) throw new RuntimeException('vault_incomplete');
            try { $actual=msw_hcb_domains($host,$row['username'],msw_hcb_unseal($row['token'],base64_decode($keys['aes'],true)))['main_domain'];
                $results[$host]=['verified'=>hash_equals($row['main_domain'],$actual),'main_domain'=>$actual];
            } catch (Throwable $e) { $results[$host]=['verified'=>false,'error'=>$e->getMessage()]; }
        }
        $receipt=['schema'=>'msw.hermes.cpanel.receipt.v1','request_id'=>$id,'checked_at'=>gmdate('c'),'results'=>$results];
        $receipt['sha256']=hash('sha256',wp_json_encode($receipt));
        msw_hcb_write('receipt',$receipt);
        return new WP_REST_Response($receipt,200);
    } catch (Throwable $e) { return new WP_Error('broker_status_failed',$e->getMessage(),['status'=>503]); }
}
add_action('rest_api_init',static function():void {
    register_rest_route('msw-hermes-cpanel/v1','/key',['methods'=>'GET','permission_callback'=>'__return_true','callback'=>static function(){try{return new WP_REST_Response(['schema'=>'msw.hermes.cpanel.key.v1','public_key'=>msw_hcb_keys()['public']],200);}catch(Throwable $e){return new WP_Error('key_unavailable','Key unavailable.',['status'=>503]);}}]);
    register_rest_route('msw-hermes-cpanel/v1','/import',['methods'=>'POST','permission_callback'=>static function(): bool { return current_user_can('manage_options'); },'callback'=>'msw_hcb_import']);
    register_rest_route('msw-hermes-cpanel/v1','/status',['methods'=>'POST','permission_callback'=>'msw_hcb_machine_permission','callback'=>'msw_hcb_status']);
    register_rest_route('msw-hermes-cpanel/v1','/receipt',['methods'=>'POST','permission_callback'=>'msw_hcb_machine_permission','callback'=>static function(){return new WP_REST_Response(msw_hcb_read('receipt'),200);}]);
});
