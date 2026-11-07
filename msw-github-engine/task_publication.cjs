#!/usr/bin/env node
'use strict';

// A final offline guard for the task workflow's existing safe-output lane.
// The caller supplies the trusted job output and an independently produced
// proof. This module neither issues execution grants nor publishes anything.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {execFileSync} = require('node:child_process');

const REPOSITORY = 'flyxpressWarszawa/KonyWarsaw';
const PROOF_SCHEMA = 'msw.coding-task-proof.v1';
const CHECK_SCHEMA = 'msw.coding-task-publication-check.v1';
const TRANSPORT_DIRECTORY = '/tmp/gh-aw';
const MAX_PATCH_BYTES = 200 * 1024;
const MAX_BUNDLE_BYTES = 32 * 1024 * 1024;
const MAX_JSON_BYTES = 1024 * 1024;
const SHA = /^[a-f0-9]{40}$/;
const HEX32 = /^[a-f0-9]{32}$/;
const HEX64 = /^[a-f0-9]{64}$/;
const BINDING_START = '<!-- msw-coding-task-binding:start -->';
const BINDING_END = '<!-- msw-coding-task-binding:end -->';
const BINDING_KEYS = ['schema', 'context_sha256', 'repository', 'queue_branch', 'queue_commit',
  'queue_head_sha', 'task_path', 'task_id', 'request_id', 'request_sha256', 'file_sha256',
  'target', 'base_branch', 'base_sha', 'allowed_paths'];
const TRUE_FLAGS = ['task_link_verified', 'local_tests_verified', 'production_hold'];
const FALSE_FLAGS = ['acceptance_verified', 'pr_verified', 'central_ack_verified',
  'merged_verified', 'deployed_verified', 'execution_authorized'];
const PROOF_KEYS = ['schema', 'status', 'context_sha256', 'binding', 'base_sha', 'commit',
  'tree', 'branch', 'changes', 'tests', 'validation_sha256', 'pr_binding_block',
  ...TRUE_FLAGS, ...FALSE_FLAGS];
const TARGET_PATHS = {
  'technical-core': ['msw-ai-technical-core/'],
  portal: ['msw-ai-centrum-robotow/app/', 'msw-ai-centrum-robotow/tests/'],
  'compute-adapters': ['msw-compute-node-adapters/'],
  research: ['msw-research-engine/'],
};

class Blocked extends Error {
  constructor(code) { super(code); this.name = 'Blocked'; this.code = code; }
}
function requireThat(condition, code) { if (!condition) throw new Blocked(code); }
function object(value) { return value !== null && typeof value === 'object' && !Array.isArray(value); }
function matches(pattern, value) { return typeof value === 'string' && pattern.test(value); }
function sha256(raw) { return crypto.createHash('sha256').update(raw).digest('hex'); }
function sameKeys(value, keys) {
  return object(value) && Object.keys(value).sort().join('\0') === [...keys].sort().join('\0');
}
function canonical(value) {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (object(value)) return '{' + Object.keys(value).sort()
    .map(key => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}';
  return JSON.stringify(value);
}

function requireExecution(env) {
  // This value must be wired from the trusted gate's job output. In this
  // proposal that gate always emits false; neither task data nor a repo flag
  // can create a permit. Check before reading any publication payload.
  requireThat(env.MSW_CODING_EXECUTION_PERMITTED === 'true', 'execution_not_permitted');
  requireThat(env.MSW_CODING_PUBLICATION_PERMITTED === 'true', 'publication_not_permitted');
  requireThat(matches(HEX64, env.EXPECTED_CONTEXT_SHA256), 'invalid_expected_context_sha256');
  requireThat(matches(SHA, env.MSW_ENGINE_BASE_SHA), 'invalid_expected_base_sha');
  requireThat(typeof env.GITHUB_REPOSITORY === 'string'
    && env.GITHUB_REPOSITORY.toLowerCase() === REPOSITORY.toLowerCase(), 'repository_mismatch');
}

function readBounded(file, maximum, transport = false) {
  requireThat(typeof file === 'string' && file.length > 0, 'missing_file');
  if (transport) {
    requireThat(path.isAbsolute(file) && path.dirname(path.resolve(file)) === TRANSPORT_DIRECTORY,
      'invalid_transport_path');
  }
  let fd;
  try {
    const before = fs.lstatSync(file);
    requireThat(before.isFile() && !before.isSymbolicLink() && before.nlink === 1,
      transport ? 'invalid_transport_file' : 'invalid_json_file');
    requireThat(before.size > 0 && before.size <= maximum, 'file_size_limit');
    if (transport) {
      requireThat(path.dirname(fs.realpathSync(file)) === TRANSPORT_DIRECTORY, 'invalid_transport_path');
    }
    fd = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
    const opened = fs.fstatSync(fd);
    requireThat(opened.dev === before.dev && opened.ino === before.ino && opened.nlink === 1,
      'file_changed_during_read');
    const buffer = Buffer.alloc(maximum + 1);
    let length = 0, count;
    do {
      count = fs.readSync(fd, buffer, length, buffer.length - length, length);
      length += count;
    } while (count > 0 && length < buffer.length);
    const after = fs.fstatSync(fd);
    requireThat(length > 0 && length <= maximum && length === before.size
      && after.size === before.size && after.mtimeMs === before.mtimeMs
      && after.ctimeMs === before.ctimeMs, 'file_changed_during_read');
    return {bytes: buffer.subarray(0, length), dev: before.dev, ino: before.ino};
  } catch (error) {
    if (error instanceof Blocked) throw error;
    throw new Blocked('file_read_failed');
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
  }
}

function readJson(file) {
  const raw = readBounded(file, MAX_JSON_BYTES).bytes;
  try {
    const text = new TextDecoder('utf-8', {fatal: true}).decode(raw);
    const value = JSON.parse(text);
    requireThat(object(value), 'invalid_json_object');
    return value;
  } catch (error) {
    if (error instanceof Blocked) throw error;
    throw new Blocked('invalid_json');
  }
}

function bindingBlock(binding) {
  // Must remain byte-for-byte identical to task_result.generate_pr_binding_block().
  return BINDING_START + '\n' + canonical(binding) + '\n' + BINDING_END;
}

function validateProof(proof, env) {
  requireThat(sameKeys(proof, PROOF_KEYS), 'invalid_proof_schema');
  requireThat(proof.schema === PROOF_SCHEMA && proof.status === 'PASS', 'proof_not_passing');
  requireThat(proof.context_sha256 === env.EXPECTED_CONTEXT_SHA256, 'context_digest_mismatch');
  requireThat(sameKeys(proof.binding, BINDING_KEYS), 'invalid_proof_binding');
  const binding = proof.binding;
  requireThat(binding.schema === 'msw.coding-task-binding.v1'
    && binding.context_sha256 === env.EXPECTED_CONTEXT_SHA256, 'context_digest_mismatch');
  requireThat(binding.repository === REPOSITORY && binding.base_branch === 'main'
    && binding.base_sha === env.MSW_ENGINE_BASE_SHA && proof.base_sha === binding.base_sha,
  'proof_base_mismatch');
  requireThat(matches(HEX32, binding.task_id) && matches(HEX64, binding.request_sha256)
    && matches(HEX64, binding.file_sha256)
    && matches(/^[A-Za-z0-9_.:-]{8,128}$/, binding.request_id),
    'invalid_proof_identity');
  requireThat(binding.queue_branch === 'msw/coding-queue'
    && matches(SHA, binding.queue_commit) && matches(SHA, binding.queue_head_sha)
    && binding.task_path === 'msw-coding-tasks/' + binding.task_id + '.json', 'invalid_proof_queue_binding');
  requireThat(Object.hasOwn(TARGET_PATHS, binding.target)
    && canonical(binding.allowed_paths) === canonical(TARGET_PATHS[binding.target]), 'invalid_proof_scope');
  requireThat(matches(SHA, proof.commit) && matches(SHA, proof.tree)
    && matches(HEX64, proof.validation_sha256), 'invalid_proof_digest');
  requireThat(TRUE_FLAGS.every(field => proof[field] === true)
    && FALSE_FLAGS.every(field => proof[field] === false),
  'invalid_proof_claims');
  requireThat(Array.isArray(proof.tests) && proof.tests.length > 0
    && proof.tests.every(test => sameKeys(test, ['id', 'checks', 'output_sha256'])
      && typeof test.id === 'string' && test.id.length > 0 && test.id.length <= 200
      && Number.isSafeInteger(test.checks) && test.checks > 0 && matches(HEX64, test.output_sha256)),
  'missing_test_evidence');
  requireThat(proof.branch === 'msw-coding-task/' + binding.task_id, 'proof_branch_mismatch');
  requireThat(Array.isArray(proof.changes) && proof.changes.length > 0 && proof.changes.length <= 12,
    'invalid_proof_changes');
  requireThat(typeof proof.pr_binding_block === 'string'
    && proof.pr_binding_block === bindingBlock(binding),
  'proof_binding_block_mismatch');
  return binding;
}

function validateRequest(item, proof, binding) {
  requireThat(!Object.hasOwn(item, 'repo') || (typeof item.repo === 'string'
    && item.repo.toLowerCase() === REPOSITORY.toLowerCase()), 'request_repository_mismatch');
  requireThat(item.branch === proof.branch, 'request_branch_mismatch');
  requireThat(item.base_commit === binding.base_sha, 'request_base_mismatch');
  for (const field of ['base', 'base_branch']) {
    requireThat(!Object.hasOwn(item, field) || item[field] === binding.base_branch, 'request_base_mismatch');
  }
  requireThat(typeof item.body === 'string' && Buffer.byteLength(item.body) <= 65536
    && !item.body.includes('\0'),
    'invalid_request_body');
  const block = proof.pr_binding_block;
  requireThat(item.body.split('<!-- msw-coding-task-binding:').length === 3
    && item.body.split(BINDING_START).length === 2
    && item.body.split(BINDING_END).length === 2
    && item.body.split(block).length === 2, 'request_binding_block_mismatch');
  const start = item.body.indexOf(block), end = start + block.length;
  requireThat((start === 0 || item.body[start - 1] === '\n')
    && (end === item.body.length || item.body[end] === '\n'), 'request_binding_block_mismatch');
}

function gitReader(cwd) {
  const execute = (args, binary = false) => {
    try {
      const result = execFileSync('git', ['-c', 'core.hooksPath=/dev/null', '-c', 'protocol.allow=never', ...args], {
        cwd, encoding: binary ? null : 'utf8', timeout: 30000, maxBuffer: MAX_JSON_BYTES,
        env: {PATH: process.env.PATH, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: '/dev/null',
          GIT_TERMINAL_PROMPT: '0'}, stdio: ['ignore', 'pipe', 'pipe'],
      });
      return binary ? result : result.trim();
    } catch (_) { throw new Blocked('git_verification_failed'); }
  };
  const git = (...args) => execute(args);
  git.raw = (...args) => execute(args, true);
  return git;
}

function verifyChanges(git, proof, binding) {
  const changes = new Map();
  for (const change of proof.changes) {
    requireThat(sameKeys(change, ['path', 'change', 'sha256', 'git_blob_sha'])
      && typeof change.path === 'string' && change.path.length > 0
      && !/[\x00-\x1f\\]/.test(change.path)
      && change.path.split('/').every(part => part && !part.startsWith('.'))
      && binding.allowed_paths.some(prefix => change.path.startsWith(prefix))
      && ['added', 'modified', 'deleted'].includes(change.change)
      && matches(SHA, change.git_blob_sha)
      && (change.change === 'deleted' ? change.sha256 === null : matches(HEX64, change.sha256))
      && !changes.has(change.path), 'invalid_proof_change');
    changes.set(change.path, change);
  }
  const fields = git('diff', '--name-status', '-z', '--no-renames', '--no-ext-diff',
    binding.base_sha, proof.commit, '--').split('\0');
  requireThat(fields.pop() === '' && fields.length === changes.size * 2, 'bundle_changes_mismatch');
  for (let index = 0; index < fields.length; index += 2) {
    const [status, file] = fields.slice(index, index + 2);
    const change = changes.get(file);
    requireThat(change && change.change === ({A: 'added', M: 'modified', D: 'deleted'})[status],
      'bundle_changes_mismatch');
    const revision = status === 'D' ? binding.base_sha : proof.commit;
    const entry = git('ls-tree', '-z', revision, '--', file);
    const match = /^(100644|100755) blob ([a-f0-9]{40})\t([^\0]+)\0$/.exec(entry);
    requireThat(match && match[3] === file && match[2] === change.git_blob_sha, 'bundle_blob_mismatch');
    if (status !== 'D') {
      requireThat(sha256(git.raw('cat-file', 'blob', change.git_blob_sha)) === change.sha256,
        'bundle_content_mismatch');
    }
  }
}

function runtimeResolver(env) {
  requireThat(typeof env.RUNNER_TEMP === 'string' && path.isAbsolute(env.RUNNER_TEMP),
    'invalid_runtime_directory');
  try {
    const runtime = require(path.join(env.RUNNER_TEMP, 'gh-aw', 'actions', 'resolve_transport_paths.cjs'));
    requireThat(typeof runtime.resolveTransportPaths === 'function', 'missing_transport_resolver');
    return runtime.resolveTransportPaths;
  } catch (error) {
    if (error instanceof Blocked) throw error;
    throw new Blocked('missing_transport_resolver');
  }
}

function verifyPublication({env = process.env, cwd = process.cwd(), proofPath,
  resolveTransportPaths} = {}) {
  requireExecution(env);
  const output = readJson(env.GH_AW_AGENT_OUTPUT);
  requireThat(Array.isArray(output.items) && output.items.every(object), 'invalid_safe_output_items');
  const requests = output.items.filter(item => item.type === 'create_pull_request');
  if (requests.length === 0) {
    return {schema: CHECK_SCHEMA, status: 'NO_PUBLICATION', publication_performed: false};
  }
  requireThat(requests.length === 1, 'multiple_pull_requests');
  const proof = readJson(proofPath || env.MSW_CANDIDATE_PROOF);
  const binding = validateProof(proof, env);
  const item = requests[0];
  validateRequest(item, proof, binding);
  const resolver = resolveTransportPaths || runtimeResolver(env);
  let transport;
  try { transport = resolver(item, item.repo || REPOSITORY); }
  catch (_) { throw new Blocked('transport_resolution_failed'); }
  requireThat(object(transport) && transport.patchPath && transport.bundlePath, 'missing_transport_files');
  requireThat(transport.patchPath !== transport.bundlePath, 'duplicate_transport_path');
  const patch = readBounded(transport.patchPath, MAX_PATCH_BYTES, true);
  const bundle = readBounded(transport.bundlePath, MAX_BUNDLE_BYTES, true);
  const patchDigest = sha256(patch.bytes), bundleDigest = sha256(bundle.bytes);
  const git = gitReader(cwd);
  git('check-ref-format', 'refs/heads/' + item.branch);
  git('bundle', 'verify', transport.bundlePath);
  const heads = git('bundle', 'unbundle', transport.bundlePath).split('\n');
  const ref = 'refs/heads/' + item.branch;
  const matching = heads.map(line => line.split(' ')).filter(parts => parts.length === 2 && parts[1] === ref);
  requireThat(matching.length === 1 && matches(SHA, matching[0][0]), 'bundle_ref_mismatch');
  const commit = matching[0][0];
  requireThat(commit === proof.commit, 'bundle_commit_mismatch');
  requireThat(git('show', '-s', '--format=%P', commit) === binding.base_sha, 'bundle_parent_mismatch');
  requireThat(git('rev-parse', commit + '^{tree}') === proof.tree, 'bundle_tree_mismatch');
  verifyChanges(git, proof, binding);
  for (const [file, maximum, first, expected] of [
    [transport.patchPath, MAX_PATCH_BYTES, patch, patchDigest],
    [transport.bundlePath, MAX_BUNDLE_BYTES, bundle, bundleDigest],
  ]) {
    const final = readBounded(file, maximum, true);
    requireThat(final.dev === first.dev && final.ino === first.ino
      && sha256(final.bytes) === expected, 'transport_changed_during_verification');
  }
  return {schema: CHECK_SCHEMA, status: 'PASS', context_sha256: proof.context_sha256,
    task_id: binding.task_id, base_sha: binding.base_sha, commit, tree: proof.tree,
    branch: proof.branch, patch_sha256: patchDigest, bundle_sha256: bundleDigest,
    publication_performed: false};
}

function main(argv = process.argv.slice(2)) {
  try {
    requireThat(argv.length === 0 || (argv.length === 2 && argv[0] === '--proof'), 'invalid_arguments');
    const result = verifyPublication({proofPath: argv[1]});
    process.stdout.write(JSON.stringify(result) + '\n');
    return 0;
  } catch (error) {
    process.stdout.write(JSON.stringify({schema: CHECK_SCHEMA, status: 'BLOCKED',
      reason: error instanceof Blocked ? error.code : 'publication_check_failed',
      publication_performed: false}) + '\n');
    return 1;
  }
}

module.exports = {Blocked, bindingBlock, verifyPublication, requireExecution, main,
  REPOSITORY, PROOF_SCHEMA, BINDING_START, BINDING_END, MAX_PATCH_BYTES, MAX_BUNDLE_BYTES};
if (require.main === module) process.exitCode = main();
