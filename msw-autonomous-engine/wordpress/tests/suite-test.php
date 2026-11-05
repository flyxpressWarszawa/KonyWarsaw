<?php
define('ABSPATH',__DIR__.'/');
define('WP_PLUGIN_DIR',__DIR__.'/fixtures/plugins');
$GLOBALS['actions']=[];$GLOBALS['options']=[];$GLOBALS['wp_version']='7.1.4';$GLOBALS['admin']=false;
function add_action($hook,$callback,...$args){$GLOBALS['actions'][$hook][]=$callback;}
function current_user_can($cap){return $GLOBALS['admin'];}
function plugin_basename($file){return str_replace(WP_PLUGIN_DIR.'/','',$file);}
function sanitize_key($value){return preg_replace('/[^a-z0-9_\-]/','',strtolower($value));}
function get_option($key,$default=false){return $GLOBALS['options'][$key]??$default;}
function get_plugins(){return $GLOBALS['plugins'];}
function is_multisite(){return false;}
function home_url($path){return 'https://mobilny-serwis-warszawa.pl'.$path;}
function wp_next_scheduled($hook){return $hook==='module_tick'?time()-2000:false;}
function check($condition,$message){if(!$condition)throw new Exception($message);}
require dirname(__DIR__).'/suite-health.php';
$GLOBALS['plugins']=[
 'example/example.php'=>['Version'=>'1.2.1','RequiresPlugins'=>'woocommerce','RequiresPHP'=>'8.1','RequiresWP'=>'6.6'],
 'woocommerce/woocommerce.php'=>['Version'=>'11.2.2'],
];
$GLOBALS['options']=[
 'active_plugins'=>['example/example.php'],
 'msw_test_state'=>['enabled'=>false,'last_error'=>['code'=>'failure','private_token'=>'NEVER_RETURN_THIS'],
  'jobs'=>[['state'=>'failed','customer'=>'PRIVATE_CUSTOMER','payload'=>'PRIVATE_PAYLOAD'],['state'=>'completed']]],
 'msw_test_lock'=>['at'=>gmdate('c',time()-3600),'token'=>'PRIVATE_LEASE']
];
MSW_Suite_Health_V1::register(WP_PLUGIN_DIR.'/example/example.php',['options'=>['msw_test_state'],'hooks'=>['module_tick'],'lock'=>'msw_test_lock']);
check(!MSW_Suite_Health_V1::allowed(),'non-administrator access allowed');
$GLOBALS['admin']=true;check(MSW_Suite_Health_V1::allowed(),'administrator refused');
$snapshot=MSW_Suite_Health_V1::snapshot();$module=$snapshot['modules'][0];
check($module['missing_dependencies']===['woocommerce'],'inactive dependency was missed');
check($module['state']['enabled']===false,'pause state lost');
check($module['state']['job_state_counts']===['failed'=>1,'completed'=>1],'job aggregates wrong');
check($module['lock_age_seconds']>=3599,'lock age absent');
check(!$snapshot['execution_verified']&&!$snapshot['publication_verified'],'status made execution claim');
$encoded=json_encode($snapshot);
foreach(['NEVER_RETURN_THIS','PRIVATE_CUSTOMER','PRIVATE_PAYLOAD','PRIVATE_LEASE'] as $secret)check(!str_contains($encoded,$secret),'private data leaked');
check(!isset($GLOBALS['actions']['init']),'new recurring execution added');
echo "suite_test_passed: permissions, dependency, state, lock age, private data and execution evidence\n";
