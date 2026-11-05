<?php
declare(strict_types=1);
namespace MSW\UnifiedEngine {
    class RemoteStore {
        public static array $kinds=['runtime.catalog','llm.generate','module.execute'];
        public static function authorise($action,$raw,$headers){return 'worker';}
        public static function config(){return ['nodes'=>['worker'=>['kinds'=>self::$kinds]]];}
    }
}
namespace {
define('ABSPATH',__DIR__.'/');define('ARRAY_A','ARRAY_A');
$options=[];$admin=true;$postCalls=0;$getCalls=0;
class WP_Error {public function __construct(public string $code,public string $message='',public array $data=[]){} }
class WP_REST_Request {
    public function __construct(private string $method='POST',private array $params=[]){ }
    public function get_method(){return $this->method;}
    public function get_body(){return json_encode($this->params);}
    public function get_json_params(){return $this->params;}
    public function get_header($key){return 'signed-test-value';}
}
class DB {
    public string $prefix='wp_';public string $options='wp_options';public string $last_error='';
    public function get_results(...$a){return [];}
    public function update(...$a){return 1;}
}
$wpdb=new DB();
function current_user_can(...$a){return $GLOBALS['admin'];}
function user_can($id,...$a){return $id===7;}
function get_current_user_id(){return 7;}
function home_url(...$a){return 'https://mobilny-serwis-warszawa.pl/';}
function get_option($key,$default=false){return $GLOBALS['options'][$key]??$default;}
function update_option($key,$value,...$a){$GLOBALS['options'][$key]=$value;return true;}
function register_activation_hook(...$a){}
function add_action(...$a){}
function is_wp_error($value){return $value instanceof WP_Error;}
function wp_json_encode($value){return json_encode($value);}
function mswai_provider_key($id){return str_repeat('x',40);}
function mswai_provider_model($id){return 'test-model';}
function wp_remote_post(...$a){$GLOBALS['postCalls']++;throw new \RuntimeException('Unexpected paid request');}
function wp_remote_get(...$a){$GLOBALS['getCalls']++;return ['http'=>200,'body'=>json_encode(['id'=>'resp_test123','status'=>'queued'])];}
function wp_remote_retrieve_response_code($r){return $r['http'];}
function wp_remote_retrieve_body($r){return $r['body'];}
function sanitize_text_field($v){return (string)$v;}
require __DIR__.'/../wordpress/msw-fotoatlas-render/msw-fotoatlas-render.php';
$tests=0;
function check($ok,$label){global $tests;if(!$ok)throw new \RuntimeException($label);$tests++;}
function invoke($method,...$args){$r=new \ReflectionMethod('MSW_FotoAtlas_Render',$method);return $r->invoke(null,...$args);}
MSW_FotoAtlas_Render::activate();
check(get_option(MSW_FotoAtlas_Render::POLICY)['owner_id']===7,'activation actor');
check(get_option(MSW_FotoAtlas_Render::POLICY)['photos_per_hour']===500,'photo target');
check(get_option(MSW_FotoAtlas_Render::POLICY)['cards_per_hour']===200,'card target');
$request=new WP_REST_Request('POST',['schema'=>'msw.fotoatlas.render.v1','action'=>'status']);
check(MSW_FotoAtlas_Render::permission($request)===true,'admin allowed');
$admin=false;check(MSW_FotoAtlas_Render::permission($request)===true,'signed scoped worker');
\MSW\UnifiedEngine\RemoteStore::$kinds=['runtime.catalog'];
check(is_wp_error(MSW_FotoAtlas_Render::permission($request)),'missing write/AI scope denied');
check(is_wp_error(MSW_FotoAtlas_Render::permission(new WP_REST_Request('GET'))),'wrong HTTP method denied');
check(is_wp_error(MSW_FotoAtlas_Render::permission(new WP_REST_Request('POST',['padding'=>str_repeat('z',3100)]))),'oversized body denied');
$policy=get_option(MSW_FotoAtlas_Render::POLICY);$policy['enabled']=false;update_option(MSW_FotoAtlas_Render::POLICY,$policy);
check(is_wp_error(MSW_FotoAtlas_Render::permission($request)),'revoked owner policy denied');
$options['msw_atlas_render_hour_'.gmdate('YmdH')]=['photos'=>499,'cards'=>199];
check(invoke('reserve','photos',500)===true,'500th photo accepted');
check(invoke('reserve','photos',500)===false,'501st photo denied');
check(invoke('reserve','cards',200)===true,'200th card accepted');
check(invoke('reserve','cards',200)===false,'201st card denied');
$lease=str_repeat('b',48);$options['msw_atlas_render_item_10']=['token_sha256'=>hash('sha256',$lease),'phase'=>'sending'];
check(is_wp_error(invoke('item',['attachment_id'=>10,'lease'=>'wrong'])),'wrong lease denied');
$p=['attachment_id'=>10,'lease'=>$lease];
check(invoke('generate',$p,false)['state']==='outcome_unknown_do_not_repeat','uncertain submit never repeated');
check($postCalls===0,'no paid retry');
$options['msw_atlas_render_item_10']['phase']='provider_pending';$options['msw_atlas_render_item_10']['response_id']='resp_test123';
check(invoke('generate',$p,true)['state']==='provider_pending','poll accepted response');
check($postCalls===0&&$getCalls===1,'poll uses GET only');
$options['msw_atlas_render_item_10']['phase']='committed';$options['msw_atlas_render_item_10']['receipt']=['state'=>'committed'];
check(invoke('commit',$p)['state']==='committed','repeated commit returns original receipt');
check(strpos(json_encode(MSW_FotoAtlas_Render::status()),str_repeat('x',40))===false,'status contains no provider key');
echo "FotoAtlas PHP: $tests checks PASS\n";
}
