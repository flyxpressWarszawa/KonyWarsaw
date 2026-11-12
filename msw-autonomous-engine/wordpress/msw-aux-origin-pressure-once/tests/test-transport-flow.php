<?php
declare(strict_types=1);
$which=$argv[1]??'candidate';
$action=$argv[2]??'claim';
$node=$argv[3]??'msw-render-auxseo-20261107-2032';
$cpu=(float)($argv[4]??'20');
$ram=(float)($argv[5]??'20');
$mode=$argv[6]??'fresh';
$root=sys_get_temp_dir().'/msw-flow-test-'.bin2hex(random_bytes(5));
$site=$root.'/public_html/mobilny-serwis-warszawa.pl';
$plugin=$site.'/wp-content/plugins/msw-unified-engine';
mkdir($plugin.'/includes',0700,true);
mkdir($root.'/.msw-cpanel-direct',0700,true);
function clean_fixture(string $path):void {
    if(is_link($path)||is_file($path)){unlink($path);return;}
    if(!is_dir($path))return;
    foreach(array_diff(scandir($path),['.','..']) as $name)clean_fixture($path.'/'.$name);
    rmdir($path);
}
register_shutdown_function(static fn()=>clean_fixture($root));
$bootstrap= <<<'PHP'
<?php
class WP_Error {
 public function get_error_data(){return ['status'=>401];}
 public function get_error_code(){return 'bad_signature';}
}
function is_wp_error($x){return $x instanceof WP_Error;}
function get_option($name,$default=[]){return $name==='active_plugins'?['msw-unified-engine/msw-unified-engine.php']:$default;}
PHP;
file_put_contents($site.'/wp-load.php',$bootstrap);
$remote= <<<'PHP'
<?php
namespace MSW\UnifiedEngine;
final class RemoteStore {
 public static function authorise($action,$raw,$headers){return $headers['node']==='deny'?new \WP_Error():$headers['node'];}
 public static function claim($node,$caps){return ['action'=>'claim','node'=>$node];}
 public static function heartbeat($node,$data){return ['action'=>'heartbeat','node'=>$node];}
 public static function complete($node,$data){return ['action'=>'complete','node'=>$node];}
 public static function receipt($node,$id){return ['action'=>'status','node'=>$node];}
}
PHP;
file_put_contents($plugin.'/includes/RemoteStore.php',$remote);
$src=$which==='original'?__DIR__.'/fixtures/transport-original.php':__DIR__.'/../payload/transport.php';
$source=file_get_contents($src);
$needle="file_get_contents('php://input', false, null, 0, 1100001)";
if(substr_count($source,$needle)!==1)throw new RuntimeException('input_anchor');
$source=str_replace($needle,"file_get_contents(__DIR__.'/fake-input.json')",$source);
$source=str_replace('    exit;', "    throw new \\RuntimeException('simulated_exit');",$source);
file_put_contents($plugin.'/transport.php',$source);
file_put_contents($plugin.'/fake-input.json',json_encode(['capabilities'=>['seo.analyze'],'job_id'=>'test']));
copy(__DIR__.'/fixtures/msw-render-account-health.php',$plugin.'/msw-render-account-health.php');
if($mode!=='missing'){
    $stamp=time()-($mode==='stale'?91:0);
    $sample=['last_result'=>['telemetry'=>['fresh'=>true,'scope'=>'account_only','source'=>'cPanel ResourceUsage/get_usages; direct uncached read',
      'measured_at'=>gmdate('c',$stamp),'cpu_percent'=>$cpu,'physical_memory_percent'=>$ram]]];
    file_put_contents($root.'/.msw-cpanel-direct/resource-guard.php',"<?php http_response_code(404); exit; ?>\n".json_encode($sample));
}
$_SERVER['REQUEST_METHOD']='POST';
$_SERVER['HTTP_X_MSWUE_ACTION']=$action;
$_SERVER['HTTP_X_MSWUE_NODE']=$node;
http_response_code(200);
try {include $plugin.'/transport.php';}
catch(RuntimeException $e){if($e->getMessage()!=='simulated_exit')throw $e;}
echo "\nSIM_STATUS=".http_response_code()."\n";
