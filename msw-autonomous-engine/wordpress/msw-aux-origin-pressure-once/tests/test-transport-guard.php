<?php
declare(strict_types=1);
function expect(bool $value, string $case): void {
    if (!$value) throw new RuntimeException($case);
}
function remove_tree(string $path): void {
    if (is_link($path) || is_file($path)) { unlink($path); return; }
    if (!is_dir($path)) return;
    foreach (array_diff(scandir($path), ['.', '..']) as $name) remove_tree($path.'/'.$name);
    rmdir($path);
}
$root = sys_get_temp_dir().'/msw-origin-guard-test-'.bin2hex(random_bytes(5));
$site = $root.'/public_html/mobilny-serwis-warszawa.pl';
$plugins = $site.'/wp-content/plugins/msw-unified-engine';
$private = $root.'/.msw-cpanel-direct';
mkdir($plugins, 0700, true);
mkdir($private, 0700, true);
$candidate = __DIR__.'/../payload/transport.php';
$sourceHelper = __DIR__.'/fixtures/msw-render-account-health.php';
copy($candidate, $plugins.'/transport.php');
copy($sourceHelper, $plugins.'/msw-render-account-health.php');
define('MSW_AUX_ORIGIN_PRESSURE_TEST', true);
require $plugins.'/transport.php';
try {
    $now=time();
    $good=['schema'=>'msw.render.account-health.v1','state'=>'fresh','measured_at'=>$now,'cpu_percent'=>12.0,'physical_memory_percent'=>13.0];
    $reader=static fn()=>$good;
    $node='msw-render-auxseo-20261107-2032';
    expect(mswue_aux_origin_safe('claim',$node,$reader,$now),'healthy accepted');
    foreach ([
        ['cpu_percent'=>70.0], ['physical_memory_percent'=>70], ['cpu_percent'=>-1],
        ['cpu_percent'=>100.0], ['cpu_percent'=>true], ['cpu_percent'=>'12'],
        ['cpu_percent'=>INF], ['cpu_percent'=>NAN], ['physical_memory_percent'=>null],
        ['state'=>'unavailable'], ['schema'=>'invalid'], ['measured_at'=>$now-91],
        ['measured_at'=>$now+1]
    ] as $bad) {
        $value=array_replace($good,$bad);
        expect(!mswue_aux_origin_safe('claim',$node,static fn()=>$value,$now),'unsafe or malformed sample rejected');
    }
    $explode=static function(){throw new RuntimeException('unavailable');};
    expect(!mswue_aux_origin_safe('claim',$node,$explode,$now),'reader exception closes gate');
    expect(mswue_aux_origin_safe('claim','msw-render-central-1',$explode,$now),'main worker unaffected');
    foreach (['complete','status','heartbeat'] as $action)
        expect(mswue_aux_origin_safe($action,$node,$explode,$now),'receipt and status unaffected');
    expect(mswue_aux_origin_safe('claim',$node,static fn()=>array_replace($good,['measured_at'=>$now-90]),$now),'90 second boundary');

    $telemetry=['last_result'=>['telemetry'=>['fresh'=>true,'scope'=>'account_only','source'=>'cPanel ResourceUsage/get_usages; direct uncached read',
        'measured_at'=>gmdate('c',$now),'cpu_percent'=>25,'physical_memory_percent'=>8]]];
    $path=$private.'/resource-guard.php';
    $prefix="<?php http_response_code(404); exit; ?>\n";
    file_put_contents($path,$prefix.json_encode($telemetry));
    clearstatcache();
    $actual=mswue_aux_account_health();
    expect(($actual['state']??'')==='fresh' && $actual['cpu_percent']===25.0,'real pinned account reader');
    expect(mswue_aux_origin_safe('claim',$node,'mswue_aux_account_health',$now),'verified reader accepted');
    $telemetry['last_result']['telemetry']['cpu_percent']=70;
    file_put_contents($path,$prefix.json_encode($telemetry)); clearstatcache();
    expect(!mswue_aux_origin_safe('claim',$node,'mswue_aux_account_health',$now),'real pressure blocks claim');
    $telemetry['last_result']['telemetry']['measured_at']=gmdate('c',$now-91);
    file_put_contents($path,$prefix.json_encode($telemetry)); clearstatcache();
    expect(!mswue_aux_origin_safe('claim',$node,'mswue_aux_account_health',$now),'stale on disk blocks claim');
    file_put_contents($plugins.'/msw-render-account-health.php',"<?php // changed\n"); clearstatcache();
    expect(mswue_aux_account_health()===[],'unexpected helper version blocks claim');
    $transport=file_get_contents($candidate);
    $auth=strpos($transport,'RemoteStore::authorise');
    $gate=strpos($transport,"if (!mswue_aux_origin_safe(");
    $claim=strpos($transport,"'claim' =>");
    expect($auth!==false && $gate>$auth && $claim>$gate,'gate follows authentication and precedes claim');
    expect(str_contains($transport,"header('Retry-After: 300')"),'bounded retry header present');
    echo "PASS: pressure, freshness, failure, scoped actions, pinned reader, and auth/claim order\n";
} finally { remove_tree($root); }
