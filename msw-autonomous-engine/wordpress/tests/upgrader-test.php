<?php
$root=sys_get_temp_dir().'/msw-suite-test-'.bin2hex(random_bytes(6));
mkdir($root.'/plugins/common',0777,true);mkdir($root.'/plugins/second',0777,true);mkdir($root.'/upgrader',0777,true);
define('ABSPATH',$root.'/');define('WP_PLUGIN_DIR',$root.'/plugins');
$GLOBALS['options']=[];$GLOBALS['change_on_backup']=false;
function current_user_can($cap){return true;}
function wp_parse_url($url,$component){return parse_url($url,$component);}
function home_url($path){return 'https://mobilny-serwis-warszawa.pl'.$path;}
function wp_generate_uuid4(){return '12345678-1234-1234-1234-123456789012';}
function get_option($key,$default=false){return array_key_exists($key,$GLOBALS['options'])?$GLOBALS['options'][$key]:$default;}
function add_option($key,$value,...$args){if(array_key_exists($key,$GLOBALS['options']))return false;$GLOBALS['options'][$key]=$value;if($key==='msw_suite_upgrade_backup_20261105'&&$GLOBALS['change_on_backup'])file_put_contents(WP_PLUGIN_DIR.'/second/second.php',"<?php\n// concurrent update\n");return true;}
function update_option($key,$value,...$args){$GLOBALS['options'][$key]=$value;return true;}
function delete_option($key){unset($GLOBALS['options'][$key]);return true;}
function get_file_data($path,$fields){preg_match('/Version:\s*([^\r\n]+)/',file_get_contents($path),$m);return ['Version'=>trim($m[1]??'')];}
function wp_clean_plugins_cache($clear){}
function register_activation_hook($file,$callback){}
function check($condition,$message){if(!$condition)throw new Exception($message);}
copy(dirname(__DIR__).'/msw-suite-upgrade/msw-suite-upgrade.php',$root.'/upgrader/upgrader.php');
copy(dirname(__DIR__).'/suite-health.php',$root.'/upgrader/suite-health.php');
$common="<?php\n/**\n * Plugin Name: Common\n * Version: 1.2.3\n */\n";
$second="<?php\n/**\n * Plugin Name: Second\n * Version: 2.3.4\n */\n";
$plan=['helper_sha256'=>hash_file('sha256',$root.'/upgrader/suite-health.php'),'sites'=>['mobilny-serwis-warszawa.pl'=>['common'=>'common/common.php','plugins'=>[
 ['file'=>'common/common.php','expected_version'=>'1.2.3'],['file'=>'second/second.php','expected_version'=>'2.3.4']]]]];
file_put_contents($root.'/upgrader/plan.json',json_encode($plan));
require $root.'/upgrader/upgrader.php';
file_put_contents(WP_PLUGIN_DIR.'/common/common.php',$common);file_put_contents(WP_PLUGIN_DIR.'/second/second.php',$second);
MSW_Suite_Upgrade_20261105::apply();
$receipt=get_option('msw_suite_upgrade_20261105');
check($receipt['state']==='applied'&&count($receipt['plugins'])===2,'complete batch missing');
check(str_contains(file_get_contents(WP_PLUGIN_DIR.'/common/common.php'),'Version: 1.2.4'),'version not changed');
check(str_contains(file_get_contents(WP_PLUGIN_DIR.'/second/second.php'),'MSW SUITE HEALTH CONTRACT'),'module integration absent');
check(!isset($GLOBALS['options']['msw_suite_upgrade_20261105_lock']),'upgrade lock retained');
MSW_Suite_Upgrade_20261105::apply();
check(count(get_option('msw_suite_upgrade_20261105')['plugins'])===2,'idempotent replay failed');
// Another portal may expose the exact companion source already upgraded above.
$sharedSource=file_get_contents(WP_PLUGIN_DIR.'/common/common.php');
$plan['sites']['mobilny-serwis-warszawa.pl']['plugins'][0]['already_applied_sha256']=hash('sha256',$sharedSource);
file_put_contents($root.'/upgrader/plan.json',json_encode($plan));
$GLOBALS['options']=[];file_put_contents(WP_PLUGIN_DIR.'/second/second.php',$second);
MSW_Suite_Upgrade_20261105::apply();
check(get_option('msw_suite_upgrade_20261105')['plugins'][0]['already_applied_shared_source']===true,'verified shared source not recognized');
check(file_get_contents(WP_PLUGIN_DIR.'/common/common.php')===$sharedSource,'shared code changed twice');
// A concurrent second-file change must abort and restore already-written files.
$GLOBALS['options']=[];$GLOBALS['change_on_backup']=true;
unlink(WP_PLUGIN_DIR.'/common/suite-health.php');
file_put_contents(WP_PLUGIN_DIR.'/common/common.php',$common);file_put_contents(WP_PLUGIN_DIR.'/second/second.php',$second);
try{MSW_Suite_Upgrade_20261105::apply();throw new Exception('concurrent change accepted');}catch(RuntimeException $error){check($error->getMessage()==='source_changed_before_write','wrong concurrent-change error');}
check(file_get_contents(WP_PLUGIN_DIR.'/common/common.php')===$common,'previous file not restored');
check(!is_file(WP_PLUGIN_DIR.'/common/suite-health.php'),'new helper not rolled back');
check(str_contains(file_get_contents(WP_PLUGIN_DIR.'/second/second.php'),'concurrent update'),'concurrent change overwritten');
check(get_option('msw_suite_upgrade_20261105')['rollback_verified']===true,'rollback proof absent');
check(get_option('msw_suite_upgrade_backup_20261105',null)===null,'failed-attempt backup retained');
echo "upgrader_test_passed: complete batch, idempotency, verified shared code, concurrent change refusal, atomic rollback and lock cleanup\n";
