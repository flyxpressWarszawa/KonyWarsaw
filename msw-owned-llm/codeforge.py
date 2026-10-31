"""A deliberately narrow source-evolution domain, NOT a Python sandbox.
The model may choose coefficients in one named pure priority function. We parse
its exact source and interpret the coefficients; generated code is never exec'd.
"""
import hashlib
import re
import time

PREFIX = 'MSW_PRIORITY_CODE_V1:'
OBJECTIVES = ('reliability', 'coverage', 'balanced')
SOURCE_PATTERN = re.compile(
    r'def msw_review_priority\(stale_hours, missing_adapter, failed_checks\):\n'
    r'    return stale_hours \+ missing_adapter \* (4|8|12) \+ failed_checks \* (24|36|48)\n?\Z'
)
GRAMMAR = r'''root ::= "def msw_review_priority(stale_hours, missing_adapter, failed_checks):\n    return stale_hours + missing_adapter * " a " + failed_checks * " b "\n"
a ::= "4" | "8" | "12"
b ::= "24" | "36" | "48"
'''
BASELINE = 'def msw_review_priority(stale_hours, missing_adapter, failed_checks):\n    return stale_hours + missing_adapter * 8 + failed_checks * 36\n'


def parse(source):
    if not isinstance(source, str) or len(source) > 240:
        raise ValueError('source_bounds')
    m = SOURCE_PATTERN.fullmatch(source)
    if not m:
        raise ValueError('source_contract')
    return int(m[1]), int(m[2])


def score(source, stale_hours, missing_adapter, failed_checks):
    a, b = parse(source)
    if type(stale_hours) is not int or not 0 <= stale_hours <= 168:
        raise ValueError('stale_hours')
    if type(missing_adapter) is not int or missing_adapter not in (0, 1):
        raise ValueError('missing_adapter')
    if type(failed_checks) is not int or not 0 <= failed_checks <= 3:
        raise ValueError('failed_checks')
    return stale_hours + missing_adapter * a + failed_checks * b


def evaluate(source):
    """Fixed acceptance criteria, independent of the model's own assessment."""
    a, b = parse(source)
    checks = {
        'zero_state': score(source, 0, 0, 0) == 0,
        'failure_over_missing_adapter': score(source, 0, 0, 1) > score(source, 0, 1, 0),
        'failure_over_missing_and_12h': score(source, 0, 0, 1) > score(source, 12, 1, 0),
        'aging_not_starved': score(source, 168, 0, 0) > score(source, 0, 1, 3),
        'bounded_maximum': score(source, 168, 1, 3) <= 324,
        'monotone_age': score(source, 72, 1, 1) > score(source, 71, 1, 1),
        'monotone_failures': score(source, 12, 1, 2) > score(source, 12, 1, 1),
        'no_negative_score': score(source, 0, 0, 0) >= 0,
    }
    return {'passed': all(checks.values()), 'checks': checks,
            'coefficients': {'missing_adapter': a, 'failed_checks': b},
            'test_scope': 'synthetic_scheduler_invariants_not_business_benchmark'}


def generate(runtime, objective):
    if objective not in OBJECTIVES:
        raise ValueError('objective')
    # No private site text, source files, passwords, customer content or arbitrary task.
    prompt = (
        'Write only the Python function for ranking technical WordPress review tasks. '
        'Function: msw_review_priority(stale_hours, missing_adapter, failed_checks). '
        'Return stale_hours + missing_adapter * A + failed_checks * B. '
        'Choose A from 4,8,12 and B from 24,36,48. '
        'Objective: ' + objective + '. A failure must outweigh missing integration. '
        'Do not add markdown, comments, imports or other statements.'
    )
    full = '<|im_start|>user\n' + prompt + '<|im_end|>\n<|im_start|>assistant\n'
    tokens = runtime.backend('/tokenize', {'content': full, 'add_special': False}).get('tokens')
    if not isinstance(tokens, list) or len(tokens) > 420:
        raise ValueError('context_limit')
    start = time.monotonic()
    result = runtime.backend('/completion', {
        'prompt': full, 'grammar': GRAMMAR, 'n_predict': 64, 'temperature': 0.2,
        'seed': 42, 'cache_prompt': False, 'stop': ['<|im_end|>']})
    source, count = result.get('content'), result.get('tokens_predicted')
    if type(count) is not int or not 0 < count <= 64:
        raise ValueError('generation_contract')
    verdict = evaluate(source)
    # Return a failed candidate as a failed candidate, never a prewritten replacement.
    return {'text': source, 'tokens_generated': count, 'prompt_tokens': len(tokens),
            'elapsed_ms': round((time.monotonic()-start)*1000),
            'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
            'candidate_evaluation': verdict, 'objective': objective,
            'source_domain': 'one_constrained_priority_function',
            'arbitrary_python_execution': False, 'business_improvement_measured': False}
