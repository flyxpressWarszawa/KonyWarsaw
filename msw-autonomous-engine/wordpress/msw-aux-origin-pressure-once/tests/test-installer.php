<?php
declare(strict_types=1);
$fixture=sys_get_temp_dir().'/msw-origin-installer-'.bin2hex(random_bytes(5));
$site=$fixture.'/public_html/mobilny-serwis-warszawa.pl';
$includes=$site.'/wp-content/plugins/msw-unified-engine';
mkdir($includes,0700,true);
define('ABSPATH',$site.'/');
define('WP_PLUGIN_DIR',$site.'/wp-content/plugins');
$GLOBALS['host']='mobilny-serwis-warszawa.pl';
$GLOBALS['settings']=[];
function home_url($path='/'){return 'https://'.$GLOBALS['host'].$path;}
function wp_parse_url($url,$part=-1){return parse_url($url,$part);}
function register_activation_hook($file,$callback){}
function esc_html($value){return $value;}
function wp_die($message,$title='',$args=[]){throw new RuntimeException($message);}
function update_option($name,$value,$autoload=false){$GLOBALS['settings'][$name]=$value;return true;}
function check(bool $ok,string $case):void {if(!$ok)throw new RuntimeException($case);}
function blocked(string $code):void {
    try {msw_aux_origin_transport_activate();throw new LogicException('not blocked');}
    catch(RuntimeException $e){check(str_contains($e->getMessage(),$code),'expected '.$code);}
}
function clean_tree(string $path):void {
    if(is_link($path)||is_file($path)){unlink($path);return;}
    if(!is_dir($path))return;
    foreach(array_diff(scandir($path),['.','..']) as $name)clean_tree($path.'/'.$name);
    rmdir($path);
}
require __DIR__.'/../msw-aux-origin-pressure-once.php';
$target=$includes.'/transport.php';
$helper=$includes.'/msw-render-account-health.php';
$healthSource=__DIR__.'/fixtures/msw-render-account-health.php';
copy(__DIR__.'/fixtures/transport-original.php',$target);
copy($healthSource,$helper);
$before=hash_file('sha256',$target);
$after=hash_file('sha256',__DIR__.'/../payload/transport.php');
try {
    $GLOBALS['host']='wrong.example';blocked('wrong_site');$GLOBALS['host']='mobilny-serwis-warszawa.pl';
    file_put_contents($helper,'changed');blocked('health_reader_mismatch');copy($healthSource,$helper);
    file_put_contents($target,'changed');blocked('source_changed');copy(__DIR__.'/fixtures/transport-original.php',$target);
    msw_aux_origin_transport_activate();clearstatcache(true,$target);
    $backup=$fixture.'/msw-doctor-quarantine/aux-origin-transport-20261108/transport.original.php';
    check(hash_file('sha256',$target)===$after,'new source readback');
    check(hash_file('sha256',$backup)===$before,'private backup readback');
    check(($GLOBALS['settings']['msw_aux_origin_transport_20261108']['state']??'')==='file_readback_verified','state');
    check(($GLOBALS['settings']['msw_aux_origin_transport_20261108']['credentials_modified_by_installer']??true)===false,'credentials unchanged');
    msw_aux_origin_transport_activate();
    check(hash_file('sha256',$target)===$after && hash_file('sha256',$backup)===$before,'idempotent retry');
    file_put_contents($backup,'changed');blocked('already_updated_backup_unverified');
    check(hash_file('sha256',$target)===$after,'no overwrite after backup conflict');
    echo "PASS: site, helper pin, source pin, backup, atomic readback and idempotency\n";
} finally {clean_tree($fixture);}
