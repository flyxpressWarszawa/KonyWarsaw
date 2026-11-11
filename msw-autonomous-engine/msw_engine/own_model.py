"""Private, deployment-selected owner model. Jobs cannot supply code or checkpoints."""
from __future__ import annotations
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re


def install(data_dir: Path, environ=None) -> dict:
    from .runtime_pack import PackError, unpack
    env = os.environ if environ is None else environ
    os.environ.pop('MSW_OWN_MODEL_ROOT', None)
    try:
        count = int(env.get('MSW_OWN_MODEL_PACK_CHUNKS', '0'))
    except (TypeError, ValueError):
        return {'state': 'blocked', 'error': 'own_model_pack_invalid', 'research_only': True}
    if not count:
        return {'state': 'not_configured', 'research_only': True}
    try:
        if not 1 <= count <= 32:
            raise PackError('own_model_pack_chunk_limit')
        encoded = ''.join(env.get(f'MSW_OWN_MODEL_PACK_{i:02d}', '') for i in range(count))
        if len(encoded) > 2_000_000:
            raise PackError('own_model_pack_size')
        raw = base64.b64decode(encoded, validate=True)
        root, manifest = unpack(raw, env.get('MSW_OWN_MODEL_PACK_SHA256', ''), data_dir)
        if [m['id'] for m in manifest['modules']] != ['msw-own-ai']:
            raise PackError('own_model_module_required')
        module = root / 'msw-own-ai'
        for required in ['model.py', 'checkpoint/checkpoint-manifest.json', 'checkpoint/train-corpus.txt', 'checkpoint/validation-corpus.txt']:
            if 'msw-own-ai/' + required not in manifest['files']:
                raise PackError('own_model_required_file')
        os.environ['MSW_OWN_MODEL_ROOT'] = str(module)
        return {'state': 'installed_verified', 'pack_sha256': env['MSW_OWN_MODEL_PACK_SHA256'], 'research_only': True,
                'business_decision_authorized': False, 'public_source_uploaded': False}
    except (ValueError, OSError, PackError):
        return {'state': 'blocked', 'error': 'own_model_pack_invalid', 'research_only': True}


def module():
    from .runtime_pack import verify_directory, MANIFEST
    configured = os.environ.get('MSW_OWN_MODEL_ROOT', '')
    if not configured:
        raise ValueError('own_model_not_configured')
    root = Path(configured)
    manifest = json.loads((root.parent / MANIFEST).read_text())
    verify_directory(root.parent, manifest['files'])
    spec = importlib.util.spec_from_file_location('msw_owner_dualmemory_private', root / 'model.py')
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return root, value


def atomic_json(path: Path, data: dict):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True))
    temporary.replace(path)


def handle(payload: dict, data_dir: Path) -> dict:
    if payload.get('schema') != 'msw.own-model.job.v1' or payload.get('owner_authorized') is not True:
        raise ValueError('own_model_authorization_required')
    action = payload.get('action')
    if action not in {'checkpoint_part', 'status', 'generate', 'train', 'training_data_status', 'train_approved'} or set(payload) - {'schema', 'owner_authorized', 'action', 'request_id', 'prompt', 'max_new_tokens', 'parent_sha256', 'steps', 'part', 'data_b64', 'train_ids', 'validation_ids', 'dataset_sha256'}:
        raise ValueError('own_model_operation_invalid')
    request = payload.get('request_id', '')
    if not isinstance(request, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{8,120}', request):
        raise ValueError('own_model_request_invalid')
    if action == 'training_data_status':
        from .rag_training import snapshot
        selected = snapshot(data_dir, payload.get('train_ids'), payload.get('validation_ids'))
        return {'schema': 'msw.dualmemory.approved-rag-snapshot.v1', 'state': 'approved_snapshot_ready',
                'dataset_sha256': selected['sha256'], 'train_records': selected['train_records'],
                'validation_records': selected['validation_records'], 'source_bytes': selected['source_bytes'],
                'training_performed': False, 'weights_modified': False, 'model_pack_required_for_training': True,
                'research_only': True}
    root, code = module()
    directory = data_dir / 'own-model'
    checkpoints = directory / 'checkpoints'
    receipts = directory / 'receipts'
    checkpoints.mkdir(parents=True, exist_ok=True, mode=0o700)
    receipts.mkdir(exist_ok=True, mode=0o700)
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    receipt = receipts / (hashlib.sha256(request.encode()).hexdigest() + '.json')
    if receipt.exists():
        previous = json.loads(receipt.read_text())
        if previous.get('payload_sha256') != fingerprint:
            raise ValueError('own_model_request_conflict')
        return previous['result']
    descriptor = json.loads((root / 'checkpoint/checkpoint-manifest.json').read_text())
    initial_digest = descriptor['sha256']
    base = checkpoints / (initial_digest + '.npz')
    if action == 'checkpoint_part':
        part = payload.get('part')
        if type(part) is not int or not 0 <= part < len(descriptor['parts']):
            raise ValueError('own_model_part_index')
        encoded = payload.get('data_b64', '')
        if not isinstance(encoded, str) or len(encoded) > 220000:
            raise ValueError('own_model_part_limit')
        raw = base64.b64decode(encoded, validate=True)
        expected = descriptor['parts'][part]
        if len(raw) != expected['bytes'] or hashlib.sha256(raw).hexdigest() != expected['sha256']:
            raise ValueError('own_model_part_digest')
        staging = directory / 'checkpoint-staging' / initial_digest
        staging.mkdir(parents=True, exist_ok=True, mode=0o700)
        staged = staging / str(part)
        if staged.exists() and staged.read_bytes() != raw:
            raise ValueError('own_model_part_conflict')
        if not staged.exists():
            temporary = staging / (str(part) + '.tmp');temporary.write_bytes(raw);temporary.replace(staged)
        complete = all((staging / str(i)).exists() for i in range(len(descriptor['parts'])))
        if complete:
            full = b''.join((staging / str(i)).read_bytes() for i in range(len(descriptor['parts'])))
            if len(full) != descriptor['bytes'] or hashlib.sha256(full).hexdigest() != initial_digest:
                raise ValueError('own_model_checkpoint_digest')
            temporary = checkpoints / (initial_digest + '.tmp.npz');temporary.write_bytes(full)
            loaded, _ = code.MSWDualMemory.load(temporary)
            if loaded.parameter_count != descriptor['parameters'] or loaded.step != descriptor['training_steps']:
                raise ValueError('own_model_checkpoint_identity')
            temporary.replace(base)
        return {'provider_ref': 'MSW-DualMemory-byte-v1', 'action': action, 'part': part, 'staged': True,
                'checkpoint_ready': complete, 'checkpoint_sha256': initial_digest, 'part_sha256': expected['sha256'],
                'tensor_readback_verified': complete, 'research_only': True, 'business_decision_authorized': False,
                'training_performed': False, 'weights_modified': False}
    if not base.exists():
        return {'provider_ref': 'MSW-DualMemory-byte-v1', 'state': 'waiting_for_pinned_checkpoint',
                'research_only': True, 'business_decision_authorized': False, 'training_performed': False}
    active = directory / 'active.json'
    reference = json.loads(active.read_text()) if active.exists() else {'sha256': initial_digest}
    digest = payload.get('parent_sha256', reference['sha256'])
    if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise ValueError('own_model_parent_invalid')
    if action == 'train_approved' and digest != reference['sha256']:
        # Signed hourly tasks must not waste CPU retraining the baseline after
        # a better current research checkpoint was accepted. No side effects.
        raise ValueError('own_model_training_stale_parent')
    path = checkpoints / (digest + '.npz')
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError('own_model_parent_digest')
    model, metadata = code.MSWDualMemory.load(path)
    result = {'provider_ref': 'MSW-DualMemory-byte-v1', 'action': action, 'request_id': request,
              'parameters': model.parameter_count, 'checkpoint_sha256': digest, 'training_steps': model.step,
              'research_only': True, 'business_decision_authorized': False, 'external_model_weights_used': False,
              'training_performed': False, 'weights_modified': False, 'persistent_disk': True}
    if action == 'generate':
        prompt = payload.get('prompt', '')
        if not isinstance(prompt, str) or len(prompt.encode()) > 2000:
            raise ValueError('own_model_prompt_limit')
        result['text'] = model.generate(prompt, max_new_tokens=max(1, min(256, int(payload.get('max_new_tokens', 96)))), seed=19)
        result['output_quality_verified'] = False
    elif action in {'train', 'train_approved'}:
        if 'parent_sha256' not in payload:
            raise ValueError('own_model_explicit_parent_required')
        if action == 'train_approved':
            # An inference-capable signed queue is NOT by itself a grant to
            # update weights. The owner pins one reviewed dataset AND parent
            # checkpoint out of band before this research-only operation.
            if (os.environ.get('MSW_OWN_MODEL_TRAIN_APPROVED') != '1'
                or os.environ.get('MSW_OWN_MODEL_TRAIN_DATASET_SHA256') != payload.get('dataset_sha256')
                or os.environ.get('MSW_OWN_MODEL_TRAIN_PARENT_SHA256') != payload.get('parent_sha256')
                or not re.fullmatch(r'[a-f0-9]{64}', os.environ.get('MSW_OWN_MODEL_TRAIN_DATASET_SHA256', ''))
                or not re.fullmatch(r'[a-f0-9]{64}', os.environ.get('MSW_OWN_MODEL_TRAIN_PARENT_SHA256', ''))):
                raise ValueError('own_model_training_out_of_band_grant_missing')
        import numpy as np
        if action == 'train_approved':
            from .rag_training import snapshot
            selected = snapshot(data_dir, payload.get('train_ids'), payload.get('validation_ids'))
            if payload.get('dataset_sha256') != selected['sha256']:
                raise ValueError('training_dataset_digest_mismatch')
            steps = max(1, min(8, int(payload.get('steps', 4))))
            train = code.encode(selected['train_text'])
            validation = code.encode(selected['validation_text'])
            training_data_sha256 = selected['sha256']
        else:
            steps = max(1, min(64, int(payload.get('steps', 32))))
            train = code.encode((root / 'checkpoint/train-corpus.txt').read_text())
            validation = code.encode((root / 'checkpoint/validation-corpus.txt').read_text())
            training_data_sha256 = hashlib.sha256((root / 'checkpoint/train-corpus.txt').read_bytes()).hexdigest()
        if len(train) < 130 or len(validation) < 97:
            raise ValueError('training_encoded_data_too_short')
        vx, vy = validation[:96].reshape(1, -1), validation[1:97].reshape(1, -1)
        before = float(model.loss(vx, vy))
        rng = np.random.default_rng(int(fingerprint[:12], 16))
        before_steps = model.step
        for _ in range(steps):
            offsets = rng.integers(0, len(train) - 65, size=2)
            x = np.stack([train[o:o+64] for o in offsets])
            y = np.stack([train[o+1:o+65] for o in offsets])
            model.train_batch(x, y)
        after = float(model.loss(vx, vy))
        if not np.isfinite(before) or not np.isfinite(after):
            raise ValueError('training_validation_nonfinite')
        candidate = directory / ('candidate-' + fingerprint + '.npz')
        model.save(candidate, metadata={'request_id': request, 'parent_sha256': digest, 'research_only': True,
                                       'training_data_sha256': training_data_sha256})
        new_digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        target = checkpoints / (new_digest + '.npz')
        candidate.replace(target)
        # Independently reopen every saved tensor before acknowledging weight changes.
        reopened, _ = code.MSWDualMemory.load(target)
        if reopened.step != before_steps + steps or any(not np.array_equal(model.p[k], reopened.p[k]) for k in model.p):
            raise ValueError('own_model_checkpoint_readback')
        promoted = after <= before and reference['sha256'] == digest
        result.update(training_performed=True, weights_modified=new_digest != digest, checkpoint_sha256=new_digest,
                      parent_sha256=digest, training_steps=reopened.step, steps_this_job=steps,
                      validation_byte_nll_before=before, validation_byte_nll_after=after,
                      validation_scope=('96 encoded bytes from a disjoint owner-reviewed approved RAG holdout; not a business benchmark' if action == 'train_approved' else '96 consecutive bytes from authored synthetic validation corpus; not a business benchmark'),
                      checkpoint_readback_verified=True, promoted_to_research_current=promoted,
                      business_quality_improvement_proven=False)
        if action == 'train_approved':
            result.update(training_corpus='approved_rag', dataset_sha256=selected['sha256'],
                          training_records=selected['train_records'],
                          validation_records=selected['validation_records'],
                          auto_training_enabled=False)
        if promoted:
            atomic_json(active, {'sha256': new_digest, 'training_steps': reopened.step, 'research_only': True})
            if json.loads(active.read_text()).get('sha256') != new_digest:
                raise ValueError('own_model_promotion_readback')
        # An interrupted unreceipted operation is recomputed from its immutable, explicit parent.
        # It cannot apply another gradient update to an already advanced checkpoint.
        atomic_json(receipt, {'payload_sha256': fingerprint, 'result': result})
        return result
    atomic_json(receipt, {'payload_sha256': fingerprint, 'result': result})
    return result


def available_handlers() -> dict:
    return {'llm.generate': handle} if os.environ.get('MSW_OWN_MODEL_ROOT') and importlib.util.find_spec('numpy') else {}


def research_status(data_dir: Path) -> dict:
    """Read-only research checkpoint evidence for the existing runtime catalog.

    Never import checkpoint Python, read model tensors into a model, inspect customer
    data or print source text. Every reported checkpoint must hash-match disk bytes.
    """
    result = {
        "state": "not_configured",
        "research_only": True,
        "training_started_by_status": False,
        "model_weight_training_verified": False,
        "checkpoint_readback_verified": False,
        "training_receipts_verified": 0,
    }
    root_path = os.environ.get("MSW_OWN_MODEL_ROOT", "")
    if not root_path:
        return result
    root = Path(root_path)
    descriptor_path = root / "checkpoint" / "checkpoint-manifest.json"
    if descriptor_path.is_symlink() or not descriptor_path.is_file():
        return dict(result, state="model_manifest_unavailable")
    try:
        descriptor = json.loads(descriptor_path.read_text())
        digest = descriptor.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("invalid_manifest_digest")
        checkpoints = data_dir / "own-model" / "checkpoints"
        base = checkpoints / (digest + ".npz")
        if base.is_symlink() or not base.is_file():
            return dict(result, state="waiting_for_pinned_checkpoint", model_pack_present=True)
        if not 0 < base.stat().st_size <= 256 * 1024 * 1024:
            raise ValueError("checkpoint_size_invalid")
        if hashlib.sha256(base.read_bytes()).hexdigest() != digest:
            raise ValueError("checkpoint_digest_mismatch")

        active_file = data_dir / "own-model" / "active.json"
        active_digest = digest
        active_steps = descriptor.get("training_steps")
        if active_file.is_symlink():
            raise ValueError("active_metadata_symlink")
        if active_file.is_file():
            active_data = json.loads(active_file.read_text())
            active_digest = active_data.get("sha256")
            active_steps = active_data.get("training_steps")
            if not isinstance(active_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", active_digest):
                raise ValueError("active_digest_invalid")
            if type(active_steps) is not int or active_steps < 0:
                raise ValueError("active_steps_invalid")
            active_path = checkpoints / (active_digest + ".npz")
            if active_path.is_symlink() or not active_path.is_file() or not 0 < active_path.stat().st_size <= 256 * 1024 * 1024:
                raise ValueError("active_checkpoint_missing")
            if hashlib.sha256(active_path.read_bytes()).hexdigest() != active_digest:
                raise ValueError("active_checkpoint_digest_mismatch")

        # Count only same-format, immutable, matching on-disk receipts. Never
        # claim business improvements or training from the initial baseline.
        receipts_dir = data_dir / "own-model" / "receipts"
        count = 0
        if receipts_dir.is_symlink():
            raise ValueError("receipts_path_symlink")
        if receipts_dir.is_dir():
            for file in sorted(receipts_dir.glob("*.json"))[:500]:
                if file.is_symlink() or file.stat().st_size > 16384:
                    continue
                try:
                    receipt = json.loads(file.read_text())
                    outcome = receipt.get("result", {})
                    if (isinstance(receipt.get("payload_sha256"), str)
                        and re.fullmatch(r"[a-f0-9]{64}", receipt["payload_sha256"])
                        and outcome.get("provider_ref") == "MSW-DualMemory-byte-v1"
                        and outcome.get("training_performed") is True
                        and outcome.get("weights_modified") is True
                        and outcome.get("checkpoint_readback_verified") is True
                        and outcome.get("action") in {"train", "train_approved"}):
                        trained_digest = outcome.get("checkpoint_sha256")
                        if not isinstance(trained_digest, str) or re.fullmatch(r"[a-f0-9]{64}", trained_digest) is None:
                            continue
                        trained_path = checkpoints / (trained_digest + ".npz")
                        if (trained_path.is_symlink() or not trained_path.is_file()
                            or not 0 < trained_path.stat().st_size <= 256 * 1024 * 1024):
                            continue
                        if hashlib.sha256(trained_path.read_bytes()).hexdigest() != trained_digest:
                            continue
                        count += 1
                except (OSError, ValueError, TypeError, AttributeError):
                    continue
        result.update(
            state="verified_research_checkpoint",
            model_pack_present=True,
            checkpoint_sha256=active_digest,
            baseline_sha256=digest,
            training_steps=active_steps if type(active_steps) is int and active_steps >= 0 else None,
            checkpoint_readback_verified=True,
            training_receipts_verified=count,
            # The presence of receipts is evidence of a saved update, not of
            # evaluation quality or the provenance of an external model.
            model_weight_training_verified=count > 0,
            business_quality_improvement_proven=False,
        )
        return result
    except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
        return dict(result, state="checkpoint_integrity_unverified", model_pack_present=True)
