<?php
if (!defined('ABSPATH')) { exit; }
/** Read-only inventory of existing engines; a discovered route is NOT proof of health. */
final class MSWDC_Engines {
    private static function adapters(){
        return [
            'neural'=>['name'=>'MSW Sieć Neuronowa','plugin'=>'msw-ai-neural-suite/msw-ai-neural-suite.php','route'=>'/msw-neural/v1/status'],
            'meta'=>['name'=>'MSW Auto-Rozwój Meta-Engine','plugin'=>'msw-ai-autoevolution/msw-ai-autoevolution.php','route'=>'/msw-evolution/v2/report'],
            'evolution'=>['name'=>'MSW Evolutionary Meta-Core','plugin'=>'msw-ai-evolutionary-meta-core/msw-ai-evolutionary-meta-core.php','route'=>'/msw-evo/v1/status'],
            'search'=>['name'=>'MSW AI Search & Learn','plugin'=>'msw-ai-search/msw-ai-search.php','route'=>'/msw-ai/v1/health'],
            'telegram'=>['name'=>'MSW Telegram','plugin'=>'msw-telegram-zgloszenia/msw-telegram-zgloszenia.php','route'=>'/msw/v1/status'],
            'photo'=>['name'=>'MSW Photo Guard','plugin'=>'','route'=>'/msw-photo-guard/v1/health'],
            'hub'=>['name'=>'MSW Python HUB','plugin'=>'msw-ai-hub/msw-ai-hub.php','route'=>'/msw-hub/v1/status'],
            'seo'=>['name'=>'MSW SEO Research','plugin'=>'msw-seo-research-engine/msw-seo-research-engine.php','route'=>'/mswseo/v2/status'],
            'services'=>['name'=>'MSW Katalog Usług','plugin'=>'msw-service-intelligence/msw-service-intelligence.php','route'=>'/mswsi/v1/status'],
            'unified'=>['name'=>'MSW Unified Bridge','plugin'=>'msw-ai-unified-bridge/msw-ai-unified-bridge.php','route'=>'/msw-unified/v1/health'],
        ];
    }
    public static function registry(){return self::adapters();}
    public static function list_engines(){
        if(!current_user_can('manage_options'))return new WP_Error('mswdc_forbidden','Brak uprawnień.',['status'=>403]);
        $plugins=(array)get_option('active_plugins',[]);
        $out=[];$routes=function_exists('rest_get_server')?array_keys((array)rest_get_server()->get_routes()):[];
        foreach(self::adapters() as $id=>$x){
            $enabled=$x['plugin']!==''?in_array($x['plugin'],$plugins,true):null;
            $registered=in_array($x['route'],$routes,true);
            $out[]=['id'=>$id,'name'=>$x['name'],'plugin_active'=>$enabled,
                    'route'=>$x['route'],'route_registered'=>$registered,'health'=>'not_checked',
                    'notice'=>'Plugin and route presence alone never confirm actual health.'];
        }
        return ['engines'=>$out,'count'=>count($out)];
    }
    public static function health($a){
        if(!current_user_can('manage_options'))return new WP_Error('mswdc_forbidden','Brak uprawnień.',['status'=>403]);
        $id=$a['engine']??'';
        if(!isset(self::adapters()[$id]))return new WP_Error('mswdc_unknown_engine','Nieznany silnik.',['status'=>400]);
        $r=self::adapters()[$id];
        if(!function_exists('rest_get_server')||!function_exists('rest_do_request'))return ['engine'=>$id,'status'=>'rest_unavailable'];
        if(!in_array($r['route'],array_keys((array)rest_get_server()->get_routes()),true))return ['engine'=>$id,'status'=>'route_not_registered'];
        // Never call /search: it can record user queries and is not a pure status endpoint.
        /* Search Health: status is checked through /health, not /search. */
        try{
            $request=new WP_REST_Request('GET',$r['route']);
            $response=rest_do_request($request);
            if(is_wp_error($response))return ['engine'=>$id,'status'=>'error','error_code'=>sanitize_key($response->get_error_code())];
            $code=(int)$response->get_status();
            if($code!==200)return ['engine'=>$id,'status'=>$code===401||$code===403?'restricted':'http_'.$code,'http_code'=>$code];
            $result=$response->get_data();if(!is_array($result))return ['engine'=>$id,'status'=>'unexpected_payload'];
            $metric=[];
            foreach(['ok','ready','active','connected','version','status','state','count','total','records','last_check','mode'] as $key){
                if(!isset($result[$key])||!is_scalar($result[$key]))continue;
                $metric[$key]=is_string($result[$key])?substr(sanitize_text_field($result[$key]),0,110):$result[$key];
            }
            $healthy=($metric['ok']??true)!==false && ($metric['ready']??true)!==false &&
                    !in_array($metric['status']??'',['error','failed','unavailable','not_ready'],true);
            return ['engine'=>$id,'status'=>$healthy?'responding':'reported_problem','http_code'=>200,
                    'metrics'=>$metric,'verified'=>true];
        }catch(Throwable $e){return ['engine'=>$id,'status'=>'exception','verified'=>false];}
    }
    /** Explicit, bounded operations against EXISTING local engine APIs. No dynamic PHP or arbitrary hooks. */
    public static function operate($a){
        if(!current_user_can('manage_options'))return new WP_Error('mswdc_forbidden','Tylko administrator.',['status'=>403]);
        $mode=$a['operation']??'';
        if(!is_string($mode)||!in_array($mode,['neural_status','neural_predict','neural_train','meta_monitor_batch','hub_health'],true))
            return new WP_Error('mswdc_operation','Nieznana operacja silnika.',['status'=>400]);
        if($mode==='neural_status'){
            if(!class_exists('MSWNeural\\Engine')||!method_exists('MSWNeural\\Engine','status'))
                return new WP_Error('mswdc_missing_engine','Neural Suite nie ma działającego API statusu.',['status'=>503]);
            try{
                $full=\MSWNeural\Engine::status();
                if(!is_array($full)||($full['version']??'')==='')return new WP_Error('mswdc_invalid_result','Brak raportu sieci.',['status'=>502]);
                $model=is_array($full['active_model']??null)?$full['active_model']:null;
                return ['engine'=>'neural','operation'=>$mode,'status'=>'completed','verified'=>true,
                    'version'=>sanitize_text_field((string)$full['version']),
                    'active_model_id'=>$model?substr((string)($model['id']??''),0,70):null,
                    'verified_examples'=>(int)($full['verified_examples']??0),
                    'notice'=>'Odczyt modelu, bez treningu ani zmiany jego wag.'];
            }catch(Throwable $e){return new WP_Error('mswdc_engine_failed','Nie można odczytać sieci.',['status'=>503]);}
        }
        if($mode==='neural_predict'){
            $phrase=trim((string)($a['text']??''));
            if(strlen($phrase)<5||strlen($phrase)>800)return new WP_Error('mswdc_text','Opis 5–800 znaków.',['status'=>400]);
            if(!class_exists('MSWNeural\\Engine')||!method_exists('MSWNeural\\Engine','predict'))
                return new WP_Error('mswdc_missing_engine','Predykcja Neural Suite niedostępna.',['status'=>503]);
            try{
                $result=\MSWNeural\Engine::predict(sanitize_text_field($phrase));
                if(!is_array($result))return new WP_Error('mswdc_invalid_result','Nieprawidłowa odpowiedź sieci.',['status'=>502]);
                // Only bounded classification fields are returned, never text sent by operator.
                $out=['engine'=>'neural','operation'=>$mode,'status'=>'completed','verified'=>true];
                foreach(['state','label','branch','network','model_id','reason'] as $key){
                    if(isset($result[$key])&&is_scalar($result[$key]))$out[$key]=substr(sanitize_text_field((string)$result[$key]),0,100);
                }
                $out['notice']='Klasyfikacja pomocnicza; nie wykonuje zlecenia klienta.';
                return $out;
            }catch(Throwable $e){return new WP_Error('mswdc_predict_failed','Sieć nie zwróciła wyniku.',['status'=>503]);}
        }
        if($mode==='neural_train'){
            if(($a['confirm_phrase']??'')!=='TRAIN:MSW')return new WP_Error('mswdc_confirmation','Wymagane TRAIN:MSW.',['status'=>400]);
            $result=self::training_step(['confirm_phrase'=>'TRAIN:MSW']);
            if(is_wp_error($result))return $result;
            $original_state=$result['status']??'unknown';
            $result['operation']=$mode;$result['verified']=in_array($original_state, ['training','promoted','rejected','waiting_for_examples'],true);
            $result['status']=$result['verified']?'completed':'unknown';
            $result['engine_state']=$original_state;
            return $result;
        }
        if($mode==='meta_monitor_batch'){
            if(($a['confirm_phrase']??'')!=='RUN:meta_monitor_batch')return new WP_Error('mswdc_confirmation','Wymagane RUN:meta_monitor_batch.',['status'=>400]);
            if(!class_exists('MSWAE_Audit')||!method_exists('MSWAE_Audit','check_next_batch'))
                return new WP_Error('mswdc_missing_engine','Brak funkcji diagnostycznej Meta-Engine.',['status'=>503]);
            try{
                $count=\MSWAE_Audit::check_next_batch(4,false);
                $valid=is_int($count)&&$count>=1&&$count<=4;
                if(!$valid)return new WP_Error('mswdc_invalid_result','Brak potwierdzenia odczytu monitoringu.',['status'=>502]);
                return ['engine'=>'meta','operation'=>$mode,'status'=>'completed','verified'=>true,'checked_engines'=>$count,
                  'notice'=>'Aktualizuje wyłącznie techniczny raport monitoringu, nie zmienia kodu wtyczek.'];
            }catch(Throwable $e){return new WP_Error('mswdc_meta_failed','Przegląd silników nie został ukończony.',['status'=>503]);}
        }
        if($mode==='hub_health'){
            if(!function_exists('mswh_connection_status'))return new WP_Error('mswdc_missing_hub','Nie wykryto autoryzowanego adaptera Python HUB.',['status'=>503]);
            try{
                $out=mswh_connection_status(true);
                if(is_wp_error($out))return new WP_Error('mswdc_hub_failure','Weryfikacja połączenia z HUB zakończyła się błędem.',['status'=>503]);
                if(!is_array($out)||($out['connected']??null)!==true||($out['cached_only']??null)!==false||($out['not_tested']??null)!==false||($out['connection_state']??null)!=='tested')return new WP_Error('mswdc_hub_unverified','Brak świeżo potwierdzonego połączenia.',['status'=>502]);
                return ['engine'=>'hub','operation'=>$mode,'status'=>'completed','verified'=>true,
                  'version'=>substr(sanitize_text_field((string)($out['version']??'')),0,40),
                  'provider_count'=>count((array)($out['providers']??[])),'connected'=>true,
                  'notice'=>'Wykonano świeży podpisany test health i search Python HUB; nie dowodzi podłączenia ChatGPT ani MSW AI CORE.'];
            }catch(Throwable $e){return new WP_Error('mswdc_hub_failure','Test Python HUB nie został ukończony.',['status'=>503]);}
        }
        return new WP_Error('mswdc_operation','Operacja nieobsługiwana.',['status'=>404]);
    }
    public static function training_step($a){
        if(!current_user_can('manage_options'))return new WP_Error('mswdc_forbidden','Brak uprawnień.',['status'=>403]);
        if(($a['confirm_phrase']??'')!=='TRAIN:MSW')return new WP_Error('mswdc_confirmation','Wymagane TRAIN:MSW.',['status'=>400]);
        if(!class_exists('MSWNeural\\Engine'))return new WP_Error('mswdc_engine_missing','Sieć neuronowa nie jest załadowana.',['status'=>503]);
        try{
            $r=\MSWNeural\Engine::train(0.8,180);
            $out=['engine'=>'neural','action'=>'train_step','status'=>$r['state']??'unknown'];
            if(isset($r['epoch']))$out['epoch']=(int)$r['epoch'];
            if(isset($r['validated_at']))$out['validated_at']=sanitize_text_field($r['validated_at']);
            $out['notice']='Training checks validation gates in existing neural engine; does not force model promotion.';
            return $out;
        }catch(Throwable $e){return new WP_Error('mswdc_training_failed','Krok treningu nie został ukończony.',['status'=>500]);}
    }
}
