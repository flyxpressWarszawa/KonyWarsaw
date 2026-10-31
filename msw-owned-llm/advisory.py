"""Surface-quality checks, not technical correctness or business-success proof.
No fallback text is returned in place of a failed model generation.
"""
import json
import re
import time
PREFIX = 'MSW is a technical service business. WordPress plugin facts: '
SUFFIX = '. Suggest one low-risk check that could improve service reliability. The facts do not prove a failure. Output advice only; do not claim work was done.'
FIELDS = {'name', 'version', 'active', 'registered_adapters', 'previous_transport_failures'}
def facts(prompt):
    if not isinstance(prompt, str) or not prompt.startswith(PREFIX):
        raise ValueError('advisory_request')
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out: raise ValueError('duplicate_metadata')
            out[key] = value
        return out
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
    label = re.sub(r'[^A-Za-z0-9 ._-]', ' ', d['name'])[:80].strip() or 'the selected plugin'
    return ('Suggest one read-only reliability check for an active WordPress plugin '
            'called ' + label + '. Answer with one short sentence starting with Check.')

def validate(text, original):
    if not isinstance(text, str) or not 30 <= len(text) <= 600: raise ValueError('advisory_length')
    words = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text.lower())
    if not 6 <= len(words) <= 70 or not re.match(r'\A(?:check|verify|inspect|review|confirm|compare|monitor|ensure)\b', text, re.I):
        raise ValueError('advisory_action')
    if text.rstrip()[-1] not in '.!?': raise ValueError('advisory_incomplete')
    if any(x in text.lower() for x in ('registered_adapters', 'previous_transport_failures', 'plugin facts:', 'output advice', 'suggest one', '<|', '```', '{', '}')):
        raise ValueError('advisory_echo')
    if re.search(r'\b(?:i|we)\s+(?:have\s+)?(?:checked|verified|fixed|changed|completed)\b', text, re.I):
        raise ValueError('advisory_false_completion')
    if re.search(r'\b(?:update|delete|disable|remove|install|activate|deactivate|restart|execute|edit|publish|send)\b', text, re.I):
        raise ValueError('advisory_mutating_action')
    source = re.findall(r'[a-z]+', original.lower())
    echo = {tuple(source[i:i+9]) for i in range(max(0, len(source)-8))}
    if any(tuple(words[i:i+9]) in echo for i in range(max(0, len(words)-8))): raise ValueError('advisory_echo')
    return {'passed': True, 'scope': 'surface_checks_not_technical_validation'}

def generate(runtime, prompt):
    question = task(prompt)
    full = ('<|im_start|>system\nYou are a helpful website maintenance assistant. '
            'Give only one read-only check. Do not make changes or claim to have checked anything.'
            '<|im_end|>\n<|im_start|>user\n' + question + '<|im_end|>\n<|im_start|>assistant\n')
    tokens = runtime.backend('/tokenize', {'content': full, 'add_special': False}).get('tokens')
    if not isinstance(tokens, list) or not len(tokens) <= 420: raise ValueError('context_limit')
    started = time.monotonic()
    out = runtime.backend('/completion', {'prompt': full, 'n_predict': 64,
        'temperature': 0.0, 'seed': 42, 'cache_prompt': False, 'stop': ['<|im_end|>', '\n']})
    text, count = out.get('content'), out.get('tokens_predicted')
    if type(count) is not int or not 0 < count <= 64 or not isinstance(text, str): raise ValueError('advisory_generation')
    text = text.strip()
    verdict = validate(text, prompt)
    return {'text': text, 'tokens_generated': count, 'prompt_tokens': len(tokens),
            'elapsed_ms': round((time.monotonic()-started)*1000),
            'advisory_quality': verdict, 'prompt_normalized': True}
