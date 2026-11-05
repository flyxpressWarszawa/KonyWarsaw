<?php
/**
 * Plugin Name: MSW Foto Atlas — Python 500/200
 * Version: 1.0.0
 */
defined('ABSPATH') || exit;

final class MSW_FotoAtlas_Python_500 {
    private const NS = 'msw-atlas-python/v1';
    private const ENGINE = 'MSW_FAU_Engine';

    public static function boot() {
        register_rest_route(self::NS, '/status', ['methods'=>'GET',
            'permission_callback'=>static fn()=>current_user_can('manage_options'),
            'callback'=>static fn()=>['version'=>'1.0.0','photo_batch'=>500,'card_batch'=>200,
                'last_scan'=>get_option('msw_fap_last_scan', null),
                'last_ack'=>get_option('msw_fap_last_ack', null)]]);
        foreach (['scan','prepare','finish'] as $op) {
            register_rest_route(self::NS, '/'.$op, ['methods'=>'POST',
                'permission_callback'=>[self::class,'authorize'], 'callback'=>[self::class,$op]]);
        }
    }

    public static function authorize($request) {
        if (current_user_can('manage_options') && $request->get_route()==='/'.self::NS.'/scan') return true;
        if (!class_exists('MSW\\UnifiedEngine\\RemoteStore')) return false;
        $node = (string)$request->get_header('x_mswue_node');
        $stamp = (string)$request->get_header('x_mswue_timestamp');
        $nonce = (string)$request->get_header('x_mswue_nonce');
        if ($node!=='msw-render-central-1' || !ctype_digit($stamp) || abs(time()-(int)$stamp)>180 ||
            !preg_match('/^[a-f0-9]{32}$/D',$nonce)) return false;
        $config = \MSW\UnifiedEngine\RemoteStore::config();
        $secret = $config['nodes'][$node]['secret'] ?? '';
        if (!is_string($secret) || strlen($secret)<32) return false;
        $action = 'fotoatlas.'.basename($request->get_route());
        $msg = implode("\n",['POST',$action,$node,$stamp,$nonce,hash('sha256',$request->get_body())]);
        return hash_equals(hash_hmac('sha256',$msg,$secret),(string)$request->get_header('x_mswue_signature'));
    }

    // Reuse the installed engine's private evidence and quality checks, pinned to its contract.
    private static function native($method,...$args) {
        if (!class_exists(self::ENGINE) || MSW_FAU_Engine::VERSION!=='1.2.0') throw new RuntimeException('engine_version');
        return (new ReflectionMethod(self::ENGINE,$method))->invoke(null,...$args);
    }
    public static function scan($request) {
        global $wpdb;
        $cursor = max(0,(int)$request->get_param('cursor'));
        $rows=$wpdb->get_results($wpdb->prepare("SELECT a.ID AS id,
            EXISTS(SELECT 1 FROM {$wpdb->prefix}msw_ppe_photos l WHERE l.attachment_id=a.ID AND l.state='verified') AS verified,
            d.state,d.reason FROM {$wpdb->posts} a LEFT JOIN {$wpdb->prefix}msw_fau_descriptions d ON d.attachment_id=a.ID
            WHERE a.ID>%d AND a.post_type='attachment' AND a.post_status='inherit'
            AND a.post_mime_type IN ('image/jpeg','image/png','image/webp')
            ORDER BY a.ID LIMIT 500",$cursor),ARRAY_A);
        if ($wpdb->last_error || !is_array($rows)) return new WP_Error('scan_failed','Odczyt kolejki niedostępny',['status'=>503]);
        $ids=[]; $end=$cursor;
        foreach ($rows as $r) {
            $end=(int)$r['id'];
            if ((int)$r['verified'] && (!$r['state'] || in_array($r['state'],['queued','atlas_remote'],true) ||
                $r['reason']==='source_not_currently_authorized')) $ids[]=$end;
        }
        $stats=['scanned'=>count($rows),'candidates'=>count($ids),'cursor'=>$end,'time'=>gmdate('c')];
        update_option('msw_fap_last_scan',$stats,false);
        return $stats+['ids'=>$ids,'eof'=>count($rows)<500];
    }
    private static function row($id) {
        global $wpdb;
        return $wpdb->get_row($wpdb->prepare("SELECT * FROM {$wpdb->prefix}msw_fau_descriptions WHERE attachment_id=%d",$id),ARRAY_A);
    }
    private static function asset($row) {
        $id=(int)$row['attachment_id'];
        if (in_array($id,[619,999,1011],true) || MSW_FAU_Editorial::locked($id) || !self::native('eligible_uncached',$row)) return [];
        foreach (['rights_verified','public_release_approved','_msw_rights_verified','_msw_public_release_approved'] as $name) {
            if (metadata_exists('post',$id,$name) && !in_array(strtolower((string)get_post_meta($id,$name,true)),['1','yes','true','approved','verified'],true)) return [];
        }
        $name=remove_accents(get_the_title($id).' '.get_post_meta($id,'_wp_attached_file',true));
        if (preg_match('/vike|paragon|faktur|paszport|prywatn|invoice|receipt|passport|private|pesel|iban/i',$name)) return [];
        return self::native('local_image',$id,$row['source_kind'],(int)$row['source_id']);
    }
    public static function prepare($request) {
        global $wpdb;
        $id=absint($request->get_param('id')); $token=(string)$request->get_param('token');
        if (!$id || !preg_match('/^[a-f0-9]{32}$/D',$token)) return ['state'=>'invalid'];
        $lock=self::native('lock');
        if (!$lock) return ['state'=>'busy'];
        try {
            $table=$wpdb->prefix.'msw_fau_descriptions';
            $row=self::row($id);
            if (!$row) {
                $ledger=$wpdb->get_row($wpdb->prepare("SELECT source_kind,source_id FROM {$wpdb->prefix}msw_ppe_photos WHERE attachment_id=%d AND state='verified' ORDER BY (source_kind='forge') DESC LIMIT 1",$id),ARRAY_A);
                if (!$ledger) return ['state'=>'held'];
                $wpdb->insert($table,$ledger+['attachment_id'=>$id,'state'=>'queued','created_at'=>current_time('mysql',true),'updated_at'=>current_time('mysql',true)]);
                $row=self::row($id);
            }
            if (!$row) return ['state'=>'busy'];
            if ($row['state']==='atlas_remote' && !hash_equals($row['last_request'],$token) && strtotime($row['next_attempt'].' UTC')>time()) return ['state'=>'busy'];
            if (!in_array($row['state'],['queued','atlas_remote'],true) && !($row['state']==='review' && $row['reason']==='source_not_currently_authorized')) return ['state'=>'held'];
            if ($row['state']==='queued' && $row['next_attempt'] && strtotime($row['next_attempt'].' UTC')>time()) return ['state'=>'held'];
            $img=self::asset($row);
            if (!$img) return ['state'=>'held'];
            $dupe=$wpdb->get_var($wpdb->prepare("SELECT attachment_id FROM $table WHERE source_sha=%s AND state IN ('ready','duplicate') AND attachment_id<>%d LIMIT 1",$img['sha'],$id));
            if ($dupe) return ['state'=>'duplicate'];
            $jpg=self::native('photo_jpeg',$img['path']);
            if (!$jpg) return ['state'=>'held'];
            $res=$wpdb->update($table,['state'=>'atlas_remote','reason'=>'python_vision','last_request'=>$token,'source_sha'=>$img['sha'],
                'source_size'=>$img['size'],'source_mtime'=>$img['mtime'],'next_attempt'=>gmdate('Y-m-d H:i:s',time()+1800)],['id'=>$row['id'],'state'=>$row['state']]);
            if ($res===false) return ['state'=>'busy'];
            return ['state'=>'claimed','sha'=>$img['sha'],'image'=>base64_encode($jpg),'schema'=>MSW_FAU_Quality::schema(),
                'prompt'=>self::native('prompt',self::native('recent',12))];
        } finally {self::native('unlock',$lock);}
    }
    public static function finish($request) {
        global $wpdb;
        $id=absint($request->get_param('id')); $token=(string)$request->get_param('token');
        if (!$id || !preg_match('/^[a-f0-9]{32}$/D',$token)) return ['state'=>'invalid'];
        $lock=self::native('lock');
        if (!$lock) return ['state'=>'busy'];
        try {
            $r=self::row($id);
            if (!$r || !hash_equals((string)$r['last_request'],$token)) return ['state'=>'obsolete'];
            if ($r['state']!=='atlas_remote') return ['state'=>$r['state']];
            $data=$request->get_param('data');
            $img=self::asset($r);
            $fields=['state'=>'review','reason'=>'python_vision_review','next_attempt'=>null,'updated_at'=>current_time('mysql',true)];
            if ($img && hash_equals($r['source_sha'],$img['sha']) && is_array($data)) {
                $check=MSW_FAU_Quality::validate($data,self::native('recent',250));
                if (!empty($check['ok'])) {
                    $d=$check['value']; $digest=hash('sha256',MSW_FAU_Quality::norm($d['tytul']));
                    $table=$wpdb->prefix.'msw_fau_descriptions';
                    $dup=$wpdb->get_var($wpdb->prepare("SELECT attachment_id FROM $table WHERE state='ready' AND (source_sha=%s OR title_sha=%s) AND attachment_id<>%d LIMIT 1",$img['sha'],$digest,$id));
                    if (!$dup && !$wpdb->last_error) {
                        $fields=array_merge($fields,['state'=>'ready','reason'=>'python_vision_validated','title_pl'=>$d['tytul'],'short_pl'=>$d['opis_krotki'],
                            'technical_pl'=>$d['opis_techniczny'],'checks_json'=>wp_json_encode($d['diagnostyka']),'observations_json'=>wp_json_encode($d['obserwacje']),
                            'alt_pl'=>$d['alt'],'service_code'=>$d['dzial'],'model'=>sanitize_text_field($request->get_param('model')),'title_sha'=>$digest]);
                    }
                }
            }
            $ok=$wpdb->update($wpdb->prefix.'msw_fau_descriptions',$fields,['id'=>$r['id'],'state'=>'atlas_remote','last_request'=>$token]);
            $read=self::row($id);
            if ($ok!==1 || !$read || $read['state']!==$fields['state'] || $read['last_request']!==$token) return ['state'=>'busy'];
            update_option('msw_fap_last_ack',['id'=>$id,'state'=>$read['state'],'at'=>gmdate('c')],false);
            return ['state'=>$read['state']];
        } finally {self::native('unlock',$lock);}
    }
}
add_action('rest_api_init',['MSW_FotoAtlas_Python_500','boot']);
