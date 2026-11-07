<?php
declare(strict_types=1);
define('MSW_ACCOUNT_HEALTH_TEST', true);
require __DIR__.'/../msw-aux-health-scope-once/payload/msw-render-account-health.php';
function check(bool $ok,string $name):void {if(!$ok) throw new RuntimeException('FAIL '.$name);}

check(msw_render_account_health_allowed('msw-render-central-1',['engine.v3.ingest']), 'existing central scope');
check(!msw_render_account_health_allowed('msw-render-central-1',['seo.analyze']), 'central missing capability');
check(msw_render_account_health_allowed('msw-render-auxseo-20261107-2032',['seo.analyze']), 'exact auxiliary scope');
check(!msw_render_account_health_allowed('msw-render-auxseo-20261107-2032',['engine.v3.ingest']), 'aux missing capability');
check(!msw_render_account_health_allowed('other-worker',['seo.analyze','engine.v3.ingest']), 'unlisted node');
check(!msw_render_account_health_allowed('msw-render-auxseo-20261107-2032','seo.analyze'), 'invalid capability type');
$now=1700000000;
$entry=['fresh'=>true,'scope'=>'account_only','source'=>'cPanel ResourceUsage/get_usages; direct uncached read',
        'measured_at'=>gmdate('c',$now-3),'cpu_percent'=>45,'physical_memory_percent'=>20];
$pack=static fn($t)=>"<?php http_response_code(404); exit; ?>\n".json_encode(['last_result'=>['telemetry'=>$t]]);
check(msw_render_account_health_parse($pack($entry),$now)['state']==='fresh', 'valid fresh telemetry');
foreach ([['measured_at'=>gmdate('c',$now-91)],['measured_at'=>gmdate('c',$now+1)],['scope'=>'server'],
          ['cpu_percent'=>101],['physical_memory_percent'=>-1],['fresh'=>false],['source'=>'unknown']] as $delta) {
    check(msw_render_account_health_parse($pack(array_replace($entry,$delta)),$now)['state']==='unavailable','invalid telemetry');
}
check(msw_render_account_health_parse('not-a-doctor-file',$now)['state']==='unavailable','invalid prefix');
echo "PASS: scoped node authorization and fresh account telemetry\n";
