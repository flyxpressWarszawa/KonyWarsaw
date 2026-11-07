<?php
declare(strict_types=1);
define('MSW_ACCOUNT_HEALTH_TEST', true);
require $argv[1];
$now = time();
$t = ['fresh'=>true,'scope'=>'account_only','source'=>'cPanel ResourceUsage/get_usages; direct',
      'measured_at'=>gmdate('c',$now),'cpu_percent'=>42,'physical_memory_percent'=>22];
$prefix = "<?php http_response_code(404); exit; ?>\n";
$make = static fn($v) => $prefix.json_encode(['last_result'=>['telemetry'=>$v]]);
$count = 0;
$check = static function($state,$raw,$label) use($now,&$count) {
    $r = msw_render_account_health_parse($raw,$now);
    if (($r['state'] ?? null) !== $state) throw new RuntimeException($label);
    if (count(array_diff(array_keys($r),['schema','state','measured_at','cpu_percent','physical_memory_percent']))) throw new RuntimeException('data_leak');
    $count++;
};
$check('fresh',$make($t),'valid');
$v=$t;$v['cpu_percent']=0;$v['physical_memory_percent']=100;$check('fresh',$make($v),'boundary');
$v=$t;$v['cpu_percent']=-1;$check('unavailable',$make($v),'negative');
$v=$t;$v['cpu_percent']=101;$check('unavailable',$make($v),'high');
$v=$t;$v['physical_memory_percent']=true;$check('unavailable',$make($v),'boolean');
$v=$t;$v['fresh']=false;$check('unavailable',$make($v),'not_fresh');
$v=$t;$v['scope']='host';$check('unavailable',$make($v),'wrong_scope');
$v=$t;$v['source']='other';$check('unavailable',$make($v),'wrong_source');
$v=$t;$v['measured_at']=gmdate('c',$now-91);$check('unavailable',$make($v),'stale');
$v=$t;$v['measured_at']=gmdate('c',$now+1);$check('unavailable',$make($v),'future');
$check('unavailable','{}','prefix');
$check('unavailable',$prefix.'{','json');
$check('unavailable',$prefix.str_repeat('x',524288),'limit');
$dir = sys_get_temp_dir().'/msw-health-test-'.bin2hex(random_bytes(6));
mkdir($dir,0700);file_put_contents($dir.'/health.php',$make($t));symlink($dir.'/health.php',$dir.'/link.php');
if (msw_render_account_health_read($dir.'/health.php',$now)['state']!=='fresh') throw new RuntimeException('file');
if (msw_render_account_health_read($dir.'/link.php',$now)['state']!=='unavailable') throw new RuntimeException('link');
if (msw_render_account_health_read($dir.'/missing.php',$now)['state']!=='unavailable') throw new RuntimeException('missing');
unlink($dir.'/link.php');unlink($dir.'/health.php');rmdir($dir);$count+=3;
echo "Account health PHP: $count checks PASS\n";
