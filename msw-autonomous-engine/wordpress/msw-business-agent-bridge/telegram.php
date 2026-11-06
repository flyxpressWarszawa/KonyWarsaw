<?php
defined('ABSPATH') || exit;

/** Customer initiated AI conversations on the existing, secret-verified bot. */
final class MSW_Business_Telegram {
    const INDEX = 'msw_business_tg_inbox_v1';
    const VERIFY = 'msw_business_telegram_verification_v1';

    public static function boot(): void {
        add_filter('rest_pre_dispatch', [self::class, 'incoming'], 20, 3);
    }
    private static function error(string $code, int $status=409) {
        return MSW_Business_Agent_Bridge::error('telegram_'.$code,$status);
    }
    public static function enabled(): bool {
        $g=(array)get_option(MSW_Business_Agent_Bridge::GRANT,[]);
        return MSW_Business_Agent_Bridge::owner() && !empty($g['inbound_customer_replies']);
    }
    public static function verification(): array {
        $s=(array)get_option(self::VERIFY,[]);
        $delivered=(array)get_option('msw_business_tg_last_delivery_v1',[]);
        return ['bot_verified'=>($s['bot_verified']??false)===true,
            'bot_username'=>$s['bot_username']??'', 'webhook_path'=>$s['webhook_path']??'',
            'customer_entry_url'=>!empty($s['bot_username'])?'https://t.me/'.$s['bot_username'].'?start=msw_ai':'',
            'inbound_customer_ai_enabled'=>self::enabled(), 'entry_mode'=>'customer_opt_in_ai_or_start_msw_ai',
            'webhook_replaced'=>false, 'customer_reply_transport_verified'=>!empty($delivered['delivery_readback_verified']),
            'last_verified_delivery_at'=>$delivered['at']??null];
    }
    private static function modeKey(int $chat): string {return 'msw_business_tg_mode_'.hash('sha256',(string)$chat);}
    private static function jobKey(string $id): string {return 'msw_business_tg_job_'.hash('sha256',$id);}
    private static function take(string $key): bool {
        global $wpdb;return (string)$wpdb->get_var($wpdb->prepare('SELECT GET_LOCK(%s,0)',$key))==='1';
    }
    private static function release(string $key): void {
        global $wpdb;$wpdb->get_var($wpdb->prepare('SELECT RELEASE_LOCK(%s)',$key));
    }
    public static function incoming($pre,$server,$request) {
        if($pre!==null||$request->get_route()!=='/msw/v1/telegram'||!self::enabled()||
            !is_callable(['MSW_Telegram_Zgloszenia','secret']))return $pre;
        $secret=MSW_Telegram_Zgloszenia::secret('MSW_TG_WEBHOOK_SECRET','telegram_secret');
        if(strlen($secret)<16||!hash_equals($secret,(string)$request->get_header('x-telegram-bot-api-secret-token')))return $pre;
        $s=MSW_Telegram_Zgloszenia::config();if(($s['mode']??'')!=='central')return $pre;
        $p=$request->get_json_params();if(!is_array($p)||!is_array($p['message']??null))return $pre;
        $m=$p['message'];$chat=$m['chat']??[];$from=$m['from']??[];
        if(($chat['type']??'')!=='private'||!is_int($chat['id']??null)||$chat['id']<=0||
            ($from['id']??null)!==$chat['id']||!empty($from['is_bot'])||
            !is_int($p['update_id']??null)||$p['update_id']<0||!is_int($m['message_id']??null))return $pre;
        $text=is_string($m['text']??null)?trim($m['text']):'';
        $entry=(bool)preg_match('/^(?:\/ai(?:@[a-zA-Z0-9_]+)?|\/start\s+msw_ai)$/D',$text);
        $modeKey=self::modeKey($chat['id']);$mode=(array)get_option($modeKey,[]);
        if(preg_match('/^\/(?:stop|cancel|zgloszenie|start)(?:\s|$)/D',$text)&&!$entry){
            if(!empty($mode['enabled'])&&$p['update_id']>=(int)($mode['last_update_id']??-1))
                update_option($modeKey,['enabled'=>false,'at'=>time(),'last_update_id'=>$p['update_id']],false);
            return $pre;
        }
        if(!$entry&&(empty($mode['enabled'])||time()-(int)($mode['at']??0)>2592000||str_starts_with($text,'/')))return $pre;
        if(!$entry&&$text==='')return $pre; // Preserve native photo and ticket handling.
        if(strlen($text)>6000)return new WP_REST_Response(['ok'=>true,'ai_message_too_long'=>true]);
        $id='tg-update-'.$p['update_id'];$key=self::jobKey($id);
        if(!self::take('msw-business-tg-index'))return self::error('queue_busy',503);
        try {
            $index=(array)get_option(self::INDEX,[]);
            // Expire the bounded private queue, without exporting customer data to RAG.
            foreach($index as $old=>$at)if(time()-(int)$at>2592000){delete_option(self::jobKey($old));unset($index[$old]);}
            if(!isset($index[$id])&&count($index)>=500)return self::error('queue_full',503);
            $row=['id'=>$id,'chat_id'=>$chat['id'],'message_id'=>$m['message_id'],'text'=>$text,
                'type'=>$entry?'welcome':'question','created_at'=>time(),'state'=>'queued',
                'session'=>$entry?$id:($mode['session']??''),
                'bot_stamp'=>hash('sha256',MSW_Telegram_Zgloszenia::secret('MSW_TG_TOKEN','telegram_token'))];
            $row['input_sha256']=hash('sha256',wp_json_encode([$row['chat_id'],$row['message_id'],$row['text'],$row['type']]));
            add_option($key,$row,'',false);
            $saved=get_option($key,[]);
            if(($saved['input_sha256']??'')!==$row['input_sha256'])return self::error('inbound_identity',409);
            $index[$id]=$saved['created_at'];update_option(self::INDEX,$index,false);
            if(get_option(self::INDEX,[])!==$index)return self::error('inbox_readback',503);
            if($p['update_id']>=(int)($mode['last_update_id']??-1)){
                $next=['enabled'=>true,'at'=>time(),'last_update_id'=>$p['update_id'],'session'=>$saved['session']];
                update_option($modeKey,$next,false);
                if(get_option($modeKey,[])!==$next)return self::error('conversation_readback',503);
            }
            return new WP_REST_Response(['ok'=>true,'ai_queued'=>true]);
        } finally {self::release('msw-business-tg-index');}
    }
    public static function redact(string $text): string {
        return preg_replace(['/\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b/i',
            '/(?<!\w)(?:\+?[0-9][ -]?){9,19}(?!\w)/','/\bsk-[A-Za-z0-9_-]{12,}/',
            '/\b[0-9]{6,}:[A-Za-z0-9_-]{20,}/'], '[dane kontaktowe pominięte]', $text);
    }
    public static function inbox(): array {
        if(!self::enabled())return ['state'=>'disabled','items'=>[]];
        $items=[];
        foreach((array)get_option(self::INDEX,[]) as $id=>$at){
            $r=(array)get_option(self::jobKey($id),[]);
            if(($r['state']??'')!=='queued'||time()-(int)$at>86400)continue;
            $mode=(array)get_option(self::modeKey((int)$r['chat_id']),[]);
            if(empty($mode['enabled'])||($mode['session']??null)!==($r['session']??''))continue;
            if(!hash_equals((string)($r['bot_stamp']??''),hash('sha256',MSW_Telegram_Zgloszenia::secret('MSW_TG_TOKEN','telegram_token'))))continue;
            $items[]=['id'=>$r['id'],'input_sha256'=>$r['input_sha256'],'type'=>$r['type'],
                'text'=>self::redact($r['text']),'created_at'=>$r['created_at']];
            if(count($items)>=2)break;
        }
        return ['items'=>$items,'destination_bound_in_wordpress'=>true,'customer_ids_exported'=>false];
    }
    public static function reply(array $p) {
        if(!self::enabled())return self::error('disabled',403);
        $id=$p['conversation_id']??'';$text=$p['reply']??'';
        if(!is_string($id)||!preg_match('/^tg-update-[0-9]{1,20}$/D',$id)||!is_string($text)||
            strlen($text)<1||strlen($text)>6000||mb_strlen($text)>3500||
            !preg_match('/^[a-f0-9]{64}$/D',(string)($p['expected_sha256']??'')))return self::error('reply_arguments');
        $lock='msw-biz-tg-'.substr(hash('sha256',$id),0,35);if(!self::take($lock))return self::error('reply_busy');
        try {
            $key=self::jobKey($id);wp_cache_delete($key,'options');$r=(array)get_option($key,[]);
            if(!hash_equals((string)($r['input_sha256']??''),$p['expected_sha256']))return self::error('reply_identity');
            if(($r['state']??'')==='sent')return hash_equals((string)$r['reply_sha256'],hash('sha256',$text))?
                ['state'=>'sent','id'=>$id,'delivery_readback_verified'=>true,'replayed'=>true]:self::error('reply_conflict');
            if(($r['state']??'')!=='queued')return self::error('reply_outcome_uncertain');
            $mode=(array)get_option(self::modeKey((int)$r['chat_id']),[]);
            if(empty($mode['enabled'])||($mode['session']??null)!==($r['session']??'')||time()-(int)$r['created_at']>86400)return self::error('conversation_closed');
            if(!hash_equals((string)($r['bot_stamp']??''),hash('sha256',MSW_Telegram_Zgloszenia::secret('MSW_TG_TOKEN','telegram_token'))))return self::error('bot_configuration_changed');
            $r['state']='sending';$r['reply_sha256']=hash('sha256',$text);update_option($key,$r,false);
            wp_cache_delete($key,'options');if(get_option($key,[])!==$r)return self::error('sending_readback',503);
            $answer=MSW_Telegram_Zgloszenia::tg_api('sendMessage',['chat_id'=>$r['chat_id'], 'text'=>$text,
                'reply_parameters'=>wp_json_encode(['message_id'=>$r['message_id'],'allow_sending_without_reply'=>true]),
                'link_preview_options'=>wp_json_encode(['is_disabled'=>true])]);
            $result=is_array($answer)?($answer['result']??[]):[];
            $ok=!is_wp_error($answer)&&($answer['ok']??false)===true&&is_int($result['message_id']??null)&&
                ($result['chat']['id']??null)===$r['chat_id']&&($result['text']??'')===$text;
            $r['state']=$ok?'sent':'outcome_uncertain';$r['delivery_at']=time();
            if($ok)$r['telegram_message_id']=$result['message_id'];
            update_option($key,$r,false);wp_cache_delete($key,'options');
            if(get_option($key,[])!==$r)return self::error('delivery_readback',503);
            if(!$ok)return self::error('delivery_outcome_uncertain',503);
            update_option('msw_business_tg_last_delivery_v1',['delivery_readback_verified'=>true,'at'=>time()],false);
            return ['state'=>'sent','id'=>$id,'delivery_readback_verified'=>true,'credentials_exported'=>false];
        } finally {self::release($lock);}
    }
}
MSW_Business_Telegram::boot();
