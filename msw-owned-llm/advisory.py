"""Bounded choice of a read-only check, NOT freeform business planning.
The result text is the model's actual JSON. Catalog descriptions are separate,
human-authored data. A failed generation is never replaced with a canned answer.
"""
import json
import time
PREFIX = 'MSW is a technical service business. WordPress plugin facts: '
SUFFIX = '. Suggest one low-risk check that could improve service reliability. The facts do not prove a failure. Output advice only; do not claim work was done.'
FIELDS = {'name', 'version', 'active', 'registered_adapters', 'previous_transport_failures'}
CATALOG = {
    'error_log_review': 'Read existing plugin-related error records; do not infer an error from missing logs.',
    'cron_callback_presence': 'Compare scheduled plugin events with registered callbacks without running them.',
    'adapter_receipt_check': 'Read existing adapter receipts and distinguish a queued task from a verified result.',
    'version_compatibility_check': 'Compare installed versions with available compatibility documentation; do not update software.'
}
GRAMMAR = r'''root ::= "{\"check_id\":\"" check "\"}"
check ::= "error_log_review" | "cron_callback_presence" | "adapter_receipt_check" | "version_compatibility_check"
'''

def unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out: raise ValueError('duplicate_key')
        out[key] = value
    return out

def facts(prompt):
    if not isinstance(prompt, str) or not prompt.startswith(PREFIX):
        raise ValueError('advisory_request')
    d, end = json.JSONDecoder(object_pairs_hook=unique).raw_decode(prompt[len(PREFIX):])
    if prompt[len(PREFIX) + end:] != SUFFIX or not isinstance(d, dict) or set(d) != FIELDS:
        raise ValueError('advisory_schema')
    if not isinstance(d['name'], str) or not 1 <= len(d['name']) <= 120 or not isinstance(d['version'], str) or len(d['version']) > 40 or d['active'] is not True:
        raise ValueError('advisory_metadata')
    for key, maximum in [('registered_adapters', 10000), ('previous_transport_failures', 3)]:
        if type(d[key]) is not int or not 0 <= d[key] <= maximum: raise ValueError('advisory_counts')
    return d

def task(prompt):
    d = facts(prompt)
    return ('Select one read-only check for this WordPress plugin: ' + json.dumps(d, ensure_ascii=True) +
            '. Transport failures do not prove plugin faults. Choices: error_log_review (read logs), '
            'cron_callback_presence (read schedule), adapter_receipt_check (read task results), '
            'version_compatibility_check (read compatibility documents). '
            'Return only a JSON object with check_id. No task is being executed.')

def validate(text, original):
    facts(original)
    if not isinstance(text, str) or not 15 <= len(text) <= 120: raise ValueError('advisory_length')
    try: d = json.loads(text, object_pairs_hook=unique)
    except (ValueError, RecursionError): raise ValueError('advisory_json') from None
    if not isinstance(d, dict) or set(d) != {'check_id'} or not isinstance(d['check_id'], str) or d['check_id'] not in CATALOG:
        raise ValueError('advisory_check_id')
    return {'passed': True, 'scope': 'catalog_membership_not_optimality_or_business_success', 'check_id': d['check_id']}

def generate(runtime, prompt):
    full = ('<|im_start|>system\nSelect one read-only inspection. Never claim an inspection was performed.'
            '<|im_end|>\n<|im_start|>user\n' + task(prompt) + '<|im_end|>\n<|im_start|>assistant\n')
    tokens = runtime.backend('/tokenize', {'content': full, 'add_special': False}).get('tokens')
    if not isinstance(tokens, list) or len(tokens) > 420: raise ValueError('context_limit')
    started = time.monotonic()
    out = runtime.backend('/completion', {'prompt': full, 'grammar': GRAMMAR, 'n_predict': 64,
        'temperature': 0.0, 'seed': 42, 'cache_prompt': False, 'stop': ['<|im_end|>']})
    text, count = out.get('content'), out.get('tokens_predicted')
    if type(count) is not int or not 0 < count <= 64 or not isinstance(text, str): raise ValueError('advisory_generation')
    text = text.strip()
    verdict = validate(text, prompt)
    return {'text': text, 'tokens_generated': count, 'prompt_tokens': len(tokens),
            'elapsed_ms': round((time.monotonic()-started)*1000),
            'advisory_quality': verdict, 'prompt_normalized': True,
            'advisory_mode': 'model_selects_catalog_check_not_freeform_reasoning',
            'catalog_description': CATALOG[verdict['check_id']],
            'catalog_description_origin': 'human_authored_not_model_generated',
            'selected_check_executed': False}
