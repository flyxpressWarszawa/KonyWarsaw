<?php
class MSW_Telegram_Zgloszenia {
    static $calls=0,$fail=false,$token='test-bot-token-value',$last=[];
    static function config(){return ['mode'=>'central'];}
    static function secret($constant,$field){return $field==='telegram_secret'?'test-telegram-secret-1234':self::$token;}
    static function tg_api($method,$payload){
        self::$calls++;self::$last=$payload;
        if(self::$fail)return new WP_Error('telegram_unavailable');
        return ['ok'=>true,'result'=>['message_id'=>990,'chat'=>['id'=>$payload['chat_id']],'text'=>$payload['text']]];
    }
}
class WP_REST_Response {function __construct(public$data,public$status=200){}}
function delete_option($key){global$options;unset($options[$key]);}
if(!function_exists('mb_strlen')){function mb_strlen($text){return strlen($text);}}
require __DIR__.'/business-agent-test.php';
class TelegramRequest {
    function __construct(public$params,public$secret='test-telegram-secret-1234',public$route='/msw/v1/telegram'){}
    function get_route(){return $this->route;}
    function get_json_params(){return $this->params;}
    function get_header($name){return $this->secret;}
}
function message($update,$text,$chat=123){return ['update_id'=>$update,'message'=>['message_id'=>$update+100,
    'chat'=>['id'=>$chat,'type'=>'private'],'from'=>['id'=>$chat,'is_bot'=>false],'text'=>$text]];}
$t='MSW_Business_Telegram';$start=$n;
check($t::incoming(null,null,new TelegramRequest(message(1,'Zwykłe zgłoszenie')))===null,'native_intake_preserved_without_ai_opt_in');
check($t::incoming(null,null,new TelegramRequest(message(2,'/ai'),'forged-secret'))===null,'forged_telegram_secret_rejected');
$group=message(3,'/ai');$group['message']['chat']['type']='group';
check($t::incoming(null,null,new TelegramRequest($group))===null,'groups_never_become_customer_chats');
$bot=message(4,'/ai');$bot['message']['from']['is_bot']=true;
check($t::incoming(null,null,new TelegramRequest($bot))===null,'bot_loops_rejected');
check($t::incoming(null,null,new TelegramRequest(message(5,'/ai')))->data['ai_queued']===true,'secret_verified_private_ai_opt_in_queued');
$t::incoming(null,null,new TelegramRequest(message(5,'/ai')));
$inbox=$t::inbox();check(count($inbox['items'])===1,'telegram_update_is_idempotent');
check(!isset($inbox['items'][0]['chat_id'])&&$inbox['customer_ids_exported']===false,'render_receives_no_destination_ids');
$job=$inbox['items'][0];$reply=['conversation_id'=>$job['id'],'expected_sha256'=>$job['input_sha256'],'reply'=>'Pomogę wybrać usługę MSW.'];
check($t::reply($reply)['delivery_readback_verified']===true,'native_telegram_ack_and_local_readback_required');
check(MSW_Telegram_Zgloszenia::$last['chat_id']===123,'destination_bound_to_verified_inbound');
$calls=MSW_Telegram_Zgloszenia::$calls;$t::reply($reply);
check(MSW_Telegram_Zgloszenia::$calls===$calls,'replayed_reply_never_sends_twice');
check(is_wp_error($t::reply(array_merge($reply,['reply'=>'Different response']))),'conflicting_reply_rejected');
check($t::verification()['customer_reply_transport_verified']===true,'status_uses_real_delivery_receipt');
$t::incoming(null,null,new TelegramRequest(message(6,'Moja poczta osoba@example.com i telefon +48 600 123 456')));
$item=$t::inbox()['items'][0];check(!str_contains($item['text'],'osoba@example.com')&&!str_contains($item['text'],'600 123'),'contact_data_redacted_before_paid_ai');
$t::incoming(null,null,new TelegramRequest(message(7,'/stop')));
check($t::inbox()['items']===[],'stop_prevents_old_queued_replies');
$t::incoming(null,null,new TelegramRequest(message(8,'/ai',456)));
$item=$t::inbox()['items'][0];$uncertain=['conversation_id'=>$item['id'],'expected_sha256'=>$item['input_sha256'],'reply'=>'Hello'];
MSW_Telegram_Zgloszenia::$fail=true;$t::reply($uncertain);$calls=MSW_Telegram_Zgloszenia::$calls;
check(is_wp_error($t::reply($uncertain))&&MSW_Telegram_Zgloszenia::$calls===$calls,'uncertain_send_is_never_reissued');
check($t::inbox()['items']===[],'uncertain_send_does_not_block_next_customer');
MSW_Telegram_Zgloszenia::$fail=false;
$t::incoming(null,null,new TelegramRequest(message(9,'/ai',789)));$item=$t::inbox()['items'][0];
MSW_Telegram_Zgloszenia::$token='rotated-to-another-bot';
check($t::inbox()['items']===[],'bot_credential_change_suspends_old_conversations');
check(is_wp_error($t::reply(['conversation_id'=>$item['id'],'expected_sha256'=>$item['input_sha256'],'reply'=>'Old bot reply'])),'old_bot_cannot_send_after_credential_change');
echo json_encode(['telegram_checks'=>$n-$start,'total_checks'=>$n,'passed'=>true]),"\n";
