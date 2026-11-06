<?php
define('ABSPATH', __DIR__ . '/'); define('WP_PLUGIN_DIR', dirname(__DIR__));
$options=[]; $actor=1;
function register_activation_hook(...$x){} function add_action(...$x){} function add_filter(...$x){} function register_post_meta(...$x){}
function get_current_user_id(){global$actor;return$actor;} function current_user_can($c){global$actor;return$actor===1;}
function user_can($u,$c){return$u===1;} function update_option($k,$v,...$x){global$options;$options[$k]=$v;}
function get_option($k,$v=false){global$options;return$options[$k]??$v;}
function add_option($k,$v,...$x){global$options;if(isset($options[$k]))return false;$options[$k]=$v;return true;}
function wp_cache_delete(...$x){} function wp_set_current_user($id){global$actor;$actor=$id;}
function is_plugin_active($p){return$p==='active/a.php';}
class MSW_Module_Autonomy{const OPTION='native_owner';}
class DB{function prepare($q,...$a){return$q;}function get_var($q){return'1';}}$wpdb=new DB;
class MSW_Central_Engine_Registry{
 static$calls=0;
 static function catalog(){return[
  'business.status'=>['plugin'=>'active/a.php','label'=>'Business status','effect'=>'read','input_schema'=>['type'=>'object']],
  'director.factory'=>['plugin'=>'active/a.php','label'=>'Factory','effect'=>'write','enabled'=>true,'input_schema'=>['type'=>'object']],
  'payment.payout'=>['plugin'=>'active/a.php','effect'=>'write'],
  'inactive.status'=>['plugin'=>'inactive/b.php','effect'=>'read']];}
 static function execute($id,$args,$rid){self::$calls++;return['verified'=>true,'api_key'=>'private-key-value'];}
}
function home_url(){return'https://mobilny-serwis-warszawa.pl';}
function wp_json_encode($x){return json_encode($x);} function is_wp_error($x){return$x instanceof WP_Error;}
function sanitize_key($x){return preg_replace('/[^a-z0-9_\-]/','',strtolower($x));}
function get_permalink($p){return'https://mobilny-serwis-warszawa.pl/product/'.(is_object($p)?$p->ID:$p);}
function get_the_title($p){return$p->post_title;} function wp_strip_all_tags($x){return strip_tags($x);}
function strip_shortcodes($x){return$x;} function clean_post_cache($x){}
function get_posts($p){return[(object)['ID'=>10,'post_title'=>'Usługa','post_content'=>'Informacja publiczna','post_type'=>'page']];}
if(!function_exists('mb_substr')){function mb_substr($x,$s,$l){return substr($x,$s,$l);}}
class WP_Error{function __construct(public$code,public$message='',public$data=[]){} function get_error_code(){return$this->code;}}
class MSW_Business_Director {static $allowed=true;static function canExecute($action){return self::$allowed;}static function settings(){return ['enabled'=>true,'owner_id'=>1,'actions'=>['factory']];}}
class MSW_Central_Engine_Core {static $enabled=true;static function settings(){return ['enabled'=>self::$enabled,'autonomy_enabled'=>true,'write_capabilities'=>['director.factory']];}}
class MSW_Central_Engine_Workflows {static function validate($kind,$input){return true;}}
class MSW_Central_Engine_Store {
 static $jobs=[];static function pending_adapter($id){return count(self::$jobs)>0;}
 static function enqueue($type,$input,$request,$owner){$job=['id'=>12,'owner_id'=>$owner,'type'=>$type,'input'=>$input,'state'=>'pending','request'=>$request];self::$jobs[]=$job;return$job;}
 static function get_job($id){return self::$jobs[0]??null;}
 static function jobs($limit){return array_reverse(self::$jobs);}static function counts(){return ['pending'=>count(self::$jobs)];}
}
class MSW_Render_Federation{static function permission($r){return new WP_Error('original_node_scope_rejected');}}
class Gateways{function payment_gateways(){return['ppcp-gateway'=>(object)['enabled'=>'yes']];}}
class Woo{function payment_gateways(){return new Gateways;}}
function WC(){return new Woo;}
class Product{
 public$status='draft';public$proof=null;public$saves=0;
 function get_id(){return 42;}function get_name(){return'Formularz MSW';}function get_sku(){return'MSW-TEST';}
 function get_status(){return$this->status;}function get_price(){return'5.00';}function is_virtual(){return true;}
 function is_downloadable(){return true;}function get_image_id(){return 12;}function get_description(){return str_repeat('Opis produktu. ',30);}
 function get_downloads(){return[];}function get_meta($k){return$this->proof;}
 function set_status($s){$this->status=$s;}function save(){$this->saves++;}
}
$product=new Product;function wc_get_product($id){global$product;return$id===42?$product:null;}
function wc_get_products($p){global$product;return[$product];}
require dirname(__DIR__).'/msw-business-agent-bridge/msw-business-agent-bridge.php';
$n=0;function check($v,$name){global$n;if(!$v)throw new RuntimeException($name);$n++;}
$c='MSW_Business_Agent_Bridge';$c::activate();
check($c::owner(),'activation_records_real_owner');
check($c::admin(),'current_owner_can_control');
check($c::permission(null)===true,'actual_admin_allowed');
$actor=0;check($c::permission(null)->get_error_code()==='original_node_scope_rejected','node_permission_never_bypassed');$actor=1;
$p=['request_id'=>'business-test-0001','business_operation'=>'status'];$s=$c::dispatch($p);
check($s['paypal']['enabled']===true,'existing_paypal_gateway_reused');
check($s['paypal']['credentials_exported']===false,'paypal_credentials_stay_in_wordpress');
check($s['paypal']['live_payment_test_verified']===false,'configuration_is_not_live_charge_test');
check($s['telegram']['webhook_replaced']===false,'existing_webhook_preserved');
check(is_wp_error($c::dispatch($p+['unexpected'=>'value'])),'extra_arguments_refused');
check(is_wp_error($c::dispatch(['request_id'=>'short','business_operation'=>'status'])),'invalid_request_identity_refused');
check(is_wp_error($c::dispatch(['request_id'=>'valid-request-1','business_operation'=>'shell'])),'no_arbitrary_shell_tool');
$context=$c::context(0);check(count($context['records'])===1&&$context['customers_or_credentials_exported']===false,'published_context_only');
$snapshot=$c::productSnapshot($product);
$job=['request_id'=>'publish-test-001','product_id'=>42,'expected_sha256'=>$snapshot['sha256']];
check($c::publish($job)->get_error_code()==='msw_business_agent_release_prerequisites','untested_product_not_published');
check($product->saves===0,'failed_release_has_no_write');
check($c::publish(array_merge($job,['expected_sha256'=>str_repeat('0',64)]))->get_error_code()==='msw_business_agent_product_changed','concurrent_product_change_rejected');
$product->status='publish';$job['expected_sha256']=$c::productSnapshot($product)['sha256'];
check($c::publish($job)['state']==='already_published'&&$product->saves===0,'existing_publication_not_duplicated');
$catalog=$c::capabilities();check(count($catalog['capabilities'])===3,'inactive_capabilities_are_excluded');
$tool=['request_id'=>'registry-tool-0001','business_operation'=>'capability.execute','capability'=>'business.status','arguments'=>[], 'catalog_sha256'=>$catalog['catalog_sha256']];
$r=$c::executeCapability($tool);check($r['storage_readback_verified']&&$r['result']['api_key']==='[credential omitted]','typed_tool_executed_with_scrubbed_receipt');
$c::executeCapability($tool);check(MSW_Central_Engine_Registry::$calls===1,'tool_replay_does_not_repeat_execution');
check(is_wp_error($c::executeCapability(array_merge($tool,['arguments'=>['changed'=>true]]))),'request_identity_conflict_rejected');
check(is_wp_error($c::executeCapability(array_merge($tool,['capability'=>'payment.payout','request_id'=>'payout-test-0001']))),'payout_not_granted_by_product_sales_request');
$factory=['request_id'=>'factory-test-0001','capability'=>'director.factory','arguments'=>[], 'catalog_sha256'=>$catalog['catalog_sha256']];
$f=$c::executeCapability($factory);
check($f['result']['state']==='native_job_pending'&&$f['result']['job_readback_verified']===true,'native_director_queue_uses_real_job');
check($f['result']['native_action_completed']===false&&count(MSW_Central_Engine_Store::$jobs)===1,'queue_receipt_is_not_product_completion');
$c::executeCapability($factory);check(count(MSW_Central_Engine_Store::$jobs)===1,'native_factory_request_deduplicated');
$f2=$c::executeCapability(array_merge($factory,['request_id'=>'factory-test-0002']));
check($f2['result']['state']==='existing_job_pending'&&count(MSW_Central_Engine_Store::$jobs)===1,'existing_factory_job_not_duplicated');
$observed=$c::directorStatus();check($observed['factory_pending']===true&&$observed['recent_jobs'][0]['id']===12,'director_status_reads_native_queue');
check($observed['product_delivery_verified']===false,'queued_factory_does_not_imply_product_delivery');
MSW_Central_Engine_Core::$enabled=false;
$f3=$c::executeCapability(array_merge($factory,['request_id'=>'factory-test-0003']));
check($f3['result']['code']==='msw_business_agent_director_policy_denied','native_stop_is_respected');
MSW_Central_Engine_Core::$enabled=true;
$options[$c::GRANT]['enabled']=false;check(!$c::owner(),'owner_stop_retained');
unset($options[$c::GRANT]);$options['native_owner']=['enabled'=>true,'owner_id'=>1];$actor=0;$c::recoverActivationGrant();check(!isset($options[$c::GRANT]),'anonymous_cannot_recover_grant');
$actor=1;$c::recoverActivationGrant();check($c::owner(),'retained_owner_recovers_incomplete_activation');
echo json_encode(['checks'=>$n,'passed'=>true]),"\n";
