<?php
declare(strict_types=1);
$fixture=sys_get_temp_dir().'/msw-origin-rollback-'.bin2hex(random_bytes(5));
$site=$fixture.'/public_html/mobilny-serwis-warszawa.pl';
$plugins=$site.'/wp-content/plugins';$includes=$plugins.'/msw-unified-engine';
$backups=$fixture.'/msw-doctor-quarantine/aux-origin-transport-20261108';
mkdir($includes,0700,true);mkdir($backups,0700,true);
define('ABSPATH',$site.'/');define('WP_PLUGIN_DIR',$plugins);
$GLOBALS['host']='mobilny-serwis-warszawa.pl';$GLOBALS['settings']=[];
function home_url($p='/'){return 'https://'.$GLOBALS['host'].$p;}
function wp_parse_url($u,$part=-1){return parse_url($u,$part);}
function register_activation_hook($f,$c){}
function esc_html($v){return $v;}
function wp_die($m,$t='',$a=[]){throw new RuntimeException($m);}
function update_option($n,$v,$a=false){$GLOBALS['settings'][$n]=$v;return true;}
function ensure_true(bool $ok,string $case):void {if(!$ok)throw new RuntimeException($case);}
function rejected(string $code):void {
    try {msw_aux_origin_rollback_activate();throw new LogicException('not rejected');}
    catch(RuntimeException $e){ensure_true(str_contains($e->getMessage(),$code),'expected '.$code);}
}
function remove_test_tree(string $p):void {
    if(is_link($p)||is_file($p)){unlink($p);return;}
    if(!is_dir($p))return;
    foreach(array_diff(scandir($p),['.','..']) as $n)remove_test_tree($p.'/'.$n);
    rmdir($p);
}
require __DIR__.'/msw-aux-origin-rollback-once.php';
$target=$includes.'/transport.php';$backup=$backups.'/transport.original.php';
$orig=__DIR__.'/../msw-aux-origin-pressure-once/tests/fixtures/transport-original.php';
$guarded=__DIR__.'/../msw-aux-origin-pressure-once/payload/transport.php';
copy($guarded,$target);copy($orig,$backup);file_put_contents($backups.'/install.lock','');
try {
    $GLOBALS['host']='wrong.example';rejected('wrong_site');$GLOBALS['host']='mobilny-serwis-warszawa.pl';
    file_put_contents($backup,'bad');rejected('backup_unverified');copy($orig,$backup);
    file_put_contents($target,'newer version');rejected('source_changed');copy($guarded,$target);
    msw_aux_origin_rollback_activate();clearstatcache(true,$target);
    ensure_true(hash_file('sha256',$target)===hash_file('sha256',$orig),'restored original');
    ensure_true(($GLOBALS['settings']['msw_aux_origin_rollback_20261108']['state']??'')==='original_readback_verified','rollback readback');
    ensure_true(($GLOBALS['settings']['msw_aux_origin_rollback_20261108']['credentials_modified_by_installer']??true)===false,'credentials unchanged');
    msw_aux_origin_rollback_activate();
    ensure_true(hash_file('sha256',$target)===hash_file('sha256',$orig),'idempotent retry');
    echo "PASS: pinned rollback, no overwrite on conflict, verified original and idempotency\n";
} finally {remove_test_tree($fixture);}
