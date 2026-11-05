<?php
namespace MSW\UnifiedEngine {
    class RemoteStore {
        public static $calls=0;
        public static function authorise($action,$raw,$headers) { self::$calls++; return $headers['signature']==='valid'?'node':new \WP_Error('auth','denied'); }
        public static function config() {return ['nodes'=>['node'=>['kinds'=>['runtime.catalog']]]];}
    }
}
namespace {
    define('ABSPATH', '/unused/');
    class WP_Error { public $code; public function __construct($c,$m='',$d=[]) {$this->code=$c;} public function get_error_code(){return $this->code;} }
    class WP_REST_Response { public $data; public function __construct($d,$s=200,$h=[]) {$this->data=$d;} }
    function is_wp_error($r){return $r instanceof WP_Error;}
    function current_user_can($c){return false;}
    function add_action(...$a){}
    function add_filter(...$a){}
    function get_plugins(){return ['one/plugin.php'=>['Name'=>'One','Version'=>'1.0']];}
    function get_option($n,$d=[]){return $n==='active_plugins'?['one/plugin.php']:$d;}
    function sanitize_text_field($s){return $s;}
    function sanitize_key($s){return preg_replace('/[^a-z0-9_-]/','',strtolower($s));}
    function home_url(){return 'https://mobilny-serwis-warszawa.pl';}
    function mswh_call(...$a){return ['ok'=>true,'version'=>'0.3.0','api_key'=>'NEVER-LEAK-KEY','providers'=>[['id'=>'local','state'=>'ready','secret'=>'NEVER-LEAK-KEY']]];}
    class Req { public $p,$raw,$signature; public function __construct($p,$signature='valid'){$this->p=$p;$this->raw=json_encode($p);$this->signature=$signature;} public function get_body(){return $this->raw;} public function get_json_params(){return $this->p;} public function get_header($k){return $k==='x-mswue-signature'?$this->signature:'test';} }
    require __DIR__.'/../msw-render-federation/msw-render-federation.php';
    $count=0;
    function check($value){global $count;if(!$value)throw new \RuntimeException('failed assertion '.($count+1));$count++;}
    $base=['schema'=>'msw.federation.v1','operation'=>'snapshot'];
    check(MSW_Render_Federation::permission(new Req($base))===true);
    check(is_wp_error(MSW_Render_Federation::permission(new Req($base,'bad'))));
    check(is_wp_error(MSW_Render_Federation::permission(new Req(['operation'=>'snapshot']))));
    check(is_wp_error(MSW_Render_Federation::permission(new Req(['schema'=>'msw.federation.v1','operation'=>'generate']))));
    $snapshot=MSW_Render_Federation::gateway(new Req($base));
    check($snapshot->data['data']['wordpress']['installed']===1);
    check($snapshot->data['data']['python_hub']['reachable']===true);
    check(!str_contains(json_encode($snapshot),'NEVER-LEAK-KEY'));
    check(is_wp_error(MSW_Render_Federation::gateway(new Req($base+['api_key'=>'forbidden']))));
    check(is_wp_error(MSW_Render_Federation::gateway(new Req(['schema'=>'msw.federation.v1','operation'=>'shell']))));
    check(is_wp_error(MSW_Render_Federation::submit(['prompt'=>'test','request_id'=>'bad/key'])));
    check(is_wp_error(MSW_Render_Federation::submit(['prompt'=>'test','request_id'=>'request-123','shell'=>'x'])));
    echo "Federation PHP assertions: $count OK\n";
}
