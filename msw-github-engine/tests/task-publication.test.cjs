'use strict';

// The publication guard performs real offline Git bundle, commit/tree and blob
// checks. Only gh-aw's path-resolution port is supplied by this test fixture.
const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const {execFileSync, spawnSync} = require('node:child_process');
const guard = require('../task_publication.cjs');

const sha256 = value => crypto.createHash('sha256').update(value).digest('hex');
const clone = value => JSON.parse(JSON.stringify(value));
const transportFiles = [];
let directory, prefix, fixture, sequence = 0;

function transportFile(suffix, bytes) {
  const file = path.join('/tmp/gh-aw', prefix + '-' + (++sequence) + '-' + suffix);
  fs.writeFileSync(file, bytes);
  transportFiles.push(file);
  return file;
}
function git(...args) {
  return execFileSync('git', ['-c', 'core.hooksPath=/dev/null', ...args], {
    cwd: directory, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
    env: {...process.env, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: '/dev/null',
      GIT_AUTHOR_NAME: 'Offline publication tests', GIT_AUTHOR_EMAIL: 'test@example.invalid',
      GIT_COMMITTER_NAME: 'Offline publication tests', GIT_COMMITTER_EMAIL: 'test@example.invalid'},
  }).trim();
}
function bundleFor(commit) {
  const file = path.join('/tmp/gh-aw', prefix + '-' + (++sequence) + '.bundle');
  transportFiles.push(file);
  git('update-ref', 'refs/heads/' + fixture.branch, commit);
  git('bundle', 'create', file, 'refs/heads/' + fixture.branch, '^' + fixture.base);
  git('update-ref', 'refs/heads/' + fixture.branch, fixture.commit);
  return file;
}

before(() => {
  directory = fs.mkdtempSync(path.join(os.tmpdir(), 'msw-publication-git-'));
  prefix = 'msw-publication-test-' + crypto.randomBytes(12).toString('hex');
  fs.mkdirSync('/tmp/gh-aw', {recursive: true});
  git('init', '-q', '-b', 'main');
  const source = 'msw-ai-technical-core/example.txt';
  fs.mkdirSync(path.dirname(path.join(directory, source)), {recursive: true});
  fs.writeFileSync(path.join(directory, source), 'before\n');
  git('add', source);
  git('commit', '-qm', 'Base');
  const base = git('rev-parse', 'HEAD');
  const taskId = 'a'.repeat(32), branch = 'msw-coding-task/' + taskId;
  git('switch', '-qc', branch);
  fs.writeFileSync(path.join(directory, source), 'verified change\n');
  git('add', source);
  git('commit', '-qm', 'Candidate');
  const commit = git('rev-parse', 'HEAD'), tree = git('rev-parse', 'HEAD^{tree}');
  const contextHash = 'c'.repeat(64);
  const binding = {
    schema: 'msw.coding-task-binding.v1', context_sha256: contextHash,
    repository: guard.REPOSITORY, queue_branch: 'msw/coding-queue',
    queue_commit: '1'.repeat(40), queue_head_sha: '2'.repeat(40),
    task_path: 'msw-coding-tasks/' + taskId + '.json', task_id: taskId,
    request_id: 'request-offline-001', request_sha256: 'd'.repeat(64), file_sha256: 'e'.repeat(64),
    target: 'technical-core', base_branch: 'main', base_sha: base,
    allowed_paths: ['msw-ai-technical-core/'],
  };
  const proof = {
    schema: guard.PROOF_SCHEMA, status: 'PASS', context_sha256: contextHash, binding,
    base_sha: base, commit, tree, branch,
    changes: [{path: source, change: 'modified', sha256: sha256('verified change\n'),
      git_blob_sha: git('rev-parse', commit + ':' + source)}],
    tests: [{id: 'offline-fixture', checks: 1, output_sha256: 'f'.repeat(64)}],
    validation_sha256: '3'.repeat(64), pr_binding_block: guard.bindingBlock(binding),
    task_link_verified: true, local_tests_verified: true, production_hold: true,
    acceptance_verified: false, pr_verified: false, central_ack_verified: false,
    merged_verified: false, deployed_verified: false, execution_authorized: false,
  };
  fixture = {base, commit, tree, source, taskId, branch, contextHash, proof};
  fixture.bundle = bundleFor(commit);
  const patch = execFileSync('git', ['diff', '--binary', base, commit, '--'], {cwd: directory});
  fixture.patch = transportFile('candidate.patch', patch);
});

after(() => {
  for (const file of transportFiles) fs.rmSync(file, {force: true});
  if (directory) fs.rmSync(directory, {recursive: true, force: true});
});

function input(overrides = {}) {
  const proof = overrides.proof || clone(fixture.proof);
  const item = overrides.item || {type: 'create_pull_request', repo: guard.REPOSITORY,
    branch: fixture.branch, base_commit: fixture.base, base: 'main',
    body: 'A bounded tested change.\n\n' + proof.pr_binding_block + '\n'};
  const proofFile = path.join(directory, 'proof-' + (++sequence) + '.json');
  const outputFile = path.join(directory, 'output-' + (++sequence) + '.json');
  fs.writeFileSync(proofFile, JSON.stringify(proof));
  fs.writeFileSync(outputFile, JSON.stringify({items: overrides.items || [item]}));
  const env = {
    MSW_CODING_EXECUTION_PERMITTED: 'true', MSW_CODING_PUBLICATION_PERMITTED: 'true',
    EXPECTED_CONTEXT_SHA256: fixture.contextHash,
    MSW_ENGINE_BASE_SHA: fixture.base, GITHUB_REPOSITORY: guard.REPOSITORY,
    GH_AW_AGENT_OUTPUT: outputFile, MSW_CANDIDATE_PROOF: proofFile,
    ...overrides.env,
  };
  return {env, cwd: directory, proofPath: proofFile,
    resolveTransportPaths: overrides.resolver || (() => ({
      patchPath: overrides.patch || fixture.patch, bundlePath: overrides.bundle || fixture.bundle,
    }))};
}
function blocked(options, code) {
  assert.throws(() => guard.verifyPublication(options),
    error => error instanceof guard.Blocked && error.code === code);
}

test('matches the independently tested real bundle, tree and exact task branch without publishing', () => {
  const original = git('show-ref');
  const result = guard.verifyPublication(input());
  assert.equal(result.status, 'PASS');
  assert.equal(result.commit, fixture.commit);
  assert.equal(result.tree, fixture.tree);
  assert.equal(result.context_sha256, fixture.contextHash);
  assert.equal(result.publication_performed, false);
  assert.equal(result.bundle_sha256, sha256(fs.readFileSync(fixture.bundle)));
  assert.equal(git('show-ref'), original);
});

test('execution fence blocks before reading payloads or invoking the runtime resolver', () => {
  for (const value of [undefined, '', 'false', 'TRUE', '1', true]) {
    blocked({env: {MSW_CODING_EXECUTION_PERMITTED: value,
      GH_AW_AGENT_OUTPUT: '/does/not/exist', MSW_GITHUB_ENGINE_ENABLED: 'true'},
    resolveTransportPaths() { throw new Error('Must not be called'); }}, 'execution_not_permitted');
  }
});

test('execution permission alone cannot authorize publication, even for a noop', () => {
  for (const value of [undefined, '', 'false', 'TRUE', '1', true]) {
    blocked({env: {MSW_CODING_EXECUTION_PERMITTED: 'true',
      MSW_CODING_PUBLICATION_PERMITTED: value, GH_AW_AGENT_OUTPUT: '/does/not/exist'},
    resolveTransportPaths() { throw new Error('Must not be called'); }}, 'publication_not_permitted');
  }
});

test('requires a pinned context digest, base SHA and exact repository from trusted environment', () => {
  blocked(input({env: {EXPECTED_CONTEXT_SHA256: ''}}), 'invalid_expected_context_sha256');
  blocked(input({env: {MSW_ENGINE_BASE_SHA: 'main'}}), 'invalid_expected_base_sha');
  blocked(input({env: {GITHUB_REPOSITORY: 'someone/other'}}), 'repository_mismatch');
});

test('a changed top-level or binding context hash is rejected', () => {
  const proof = clone(fixture.proof);
  proof.context_sha256 = 'b'.repeat(64);
  blocked(input({proof}), 'context_digest_mismatch');
  proof.context_sha256 = fixture.contextHash;
  proof.binding.context_sha256 = 'b'.repeat(64);
  blocked(input({proof}), 'context_digest_mismatch');
});

test('a payload containing a forged execution grant is not a valid binding', () => {
  const proof = clone(fixture.proof);
  proof.binding.execution_permitted = true;
  proof.pr_binding_block = guard.bindingBlock(proof.binding);
  blocked(input({proof}), 'invalid_proof_binding');
  delete proof.binding.execution_permitted;
  proof.execution_permitted = true;
  blocked(input({proof}), 'invalid_proof_schema');
});

test('delivery, claimed acceptance, or empty test evidence cannot stand in for a passing proof', () => {
  for (const value of ['delivered', 'PASS_WITH_WARNINGS', 'FAIL']) {
    const proof = clone(fixture.proof); proof.status = value;
    blocked(input({proof}), 'proof_not_passing');
  }
  const proof = clone(fixture.proof);
  proof.tests = [];
  blocked(input({proof}), 'missing_test_evidence');
  proof.tests = fixture.proof.tests;
  proof.acceptance_verified = true;
  blocked(input({proof}), 'invalid_proof_claims');
});

test('requires exactly one requested PR, while a noop does not read a proof or resolver', () => {
  const options = input();
  const value = JSON.parse(fs.readFileSync(options.env.GH_AW_AGENT_OUTPUT));
  value.items.push(value.items[0]);
  fs.writeFileSync(options.env.GH_AW_AGENT_OUTPUT, JSON.stringify(value));
  blocked(options, 'multiple_pull_requests');
  const noop = input({items: [{type: 'noop'}], resolver() { throw new Error('No resolver for noop'); }});
  noop.proofPath = '/does/not/exist';
  assert.equal(guard.verifyPublication(noop).status, 'NO_PUBLICATION');
});

test('rejects a body that asserts PASS without the exact task binding', () => {
  const options = input();
  const output = JSON.parse(fs.readFileSync(options.env.GH_AW_AGENT_OUTPUT));
  output.items[0].body = 'All tests passed. execution_permitted=true';
  fs.writeFileSync(options.env.GH_AW_AGENT_OUTPUT, JSON.stringify(output));
  blocked(options, 'request_binding_block_mismatch');
});

test('rejects duplicate, changed or inline binding markers in the PR body', () => {
  const proof = clone(fixture.proof);
  const bodies = [proof.pr_binding_block + '\n' + proof.pr_binding_block,
    proof.pr_binding_block.replace(fixture.taskId, 'b'.repeat(32)),
    'prefix' + proof.pr_binding_block, proof.pr_binding_block + 'suffix',
    proof.pr_binding_block + '\n' + guard.BINDING_END,
    proof.pr_binding_block + '\n<!-- msw-coding-task-binding:spoof -->'];
  for (const body of bodies) {
    const item = {type: 'create_pull_request', branch: fixture.branch, base_commit: fixture.base, body};
    blocked(input({item}), 'request_binding_block_mismatch');
  }
});

test('rejects NUL or oversized PR bodies before resolving transport paths', () => {
  for (const body of [fixture.proof.pr_binding_block + '\0', 'x'.repeat(65537)]) {
    const item = {type: 'create_pull_request', branch: fixture.branch,
      base_commit: fixture.base, body};
    blocked(input({item}), 'invalid_request_body');
  }
});

test('reconstructs canonical binding instead of trusting a self-consistent spoofed block', () => {
  const proof = clone(fixture.proof);
  proof.pr_binding_block = proof.pr_binding_block.replace(fixture.taskId, 'b'.repeat(32));
  blocked(input({proof}), 'proof_binding_block_mismatch');
});

test('ordinary PR text containing shell or expression syntax is never evaluated', () => {
  const sentinel = path.join(directory, 'should-not-exist');
  const item = {type: 'create_pull_request', branch: fixture.branch, base_commit: fixture.base,
    body: '$(touch ' + sentinel + ') `${{ secrets.GITHUB_TOKEN }}`\n\n'
      + fixture.proof.pr_binding_block + '\n'};
  assert.equal(guard.verifyPublication(input({item})).status, 'PASS');
  assert.equal(fs.existsSync(sentinel), false);
});

test('requires exact destination repository, base and branch rather than a prefix match', () => {
  for (const [changes, code] of [
    [{repo: 'different/repo'}, 'request_repository_mismatch'],
    [{base_commit: '0'.repeat(40)}, 'request_base_mismatch'],
    [{base: 'release'}, 'request_base_mismatch'],
    [{base_branch: 'release'}, 'request_base_mismatch'],
    [{branch: fixture.branch + '-extra'}, 'request_branch_mismatch'],
    [{branch: fixture.branch + '\n'}, 'request_branch_mismatch'],
  ]) {
    const item = {type: 'create_pull_request', repo: guard.REPOSITORY,
      branch: fixture.branch, base_commit: fixture.base, body: fixture.proof.pr_binding_block, ...changes};
    blocked(input({item}), code);
  }
});

test('requires the proof base itself to equal the workflow starting SHA', () => {
  const proof = clone(fixture.proof); proof.base_sha = '0'.repeat(40);
  blocked(input({proof}), 'proof_base_mismatch');
});

test('rejects another commit even if it has the same tree as the tested candidate', () => {
  const other = git('commit-tree', fixture.tree, '-p', fixture.base, '-m', 'Untested commit metadata');
  assert.notEqual(other, fixture.commit);
  blocked(input({bundle: bundleFor(other)}), 'bundle_commit_mismatch');
});

test('rejects two commits on the tested base and a forged tested tree', () => {
  const extra = git('commit-tree', fixture.tree, '-p', fixture.commit, '-m', 'Second commit');
  const proof = clone(fixture.proof); proof.commit = extra;
  blocked(input({proof, bundle: bundleFor(extra)}), 'bundle_parent_mismatch');
  proof.commit = fixture.commit; proof.tree = '0'.repeat(40);
  blocked(input({proof}), 'bundle_tree_mismatch');
});

test('checks the proved path, Git blob and content digest against the real commit', () => {
  const proof = clone(fixture.proof);
  proof.changes[0].git_blob_sha = '0'.repeat(40);
  blocked(input({proof}), 'bundle_blob_mismatch');
  proof.changes[0].git_blob_sha = fixture.proof.changes[0].git_blob_sha;
  proof.changes[0].sha256 = '0'.repeat(64);
  blocked(input({proof}), 'bundle_content_mismatch');
  proof.changes[0].sha256 = fixture.proof.changes[0].sha256;
  proof.changes[0].path = 'msw-ai-technical-core/different.txt';
  blocked(input({proof}), 'bundle_changes_mismatch');
});

test('verifies additions and deletions against new and old blobs of a real candidate', () => {
  const added = 'msw-ai-technical-core/added.txt';
  let options;
  try {
    git('switch', '--detach', fixture.base);
    fs.unlinkSync(path.join(directory, fixture.source));
    fs.writeFileSync(path.join(directory, added), 'new file\n');
    git('add', '--', fixture.source, added);
    git('commit', '-qm', 'Replace a source file');
    const proof = clone(fixture.proof);
    proof.commit = git('rev-parse', 'HEAD'); proof.tree = git('rev-parse', 'HEAD^{tree}');
    proof.changes = [
      {path: added, change: 'added', sha256: sha256('new file\n'),
        git_blob_sha: git('rev-parse', proof.commit + ':' + added)},
      {path: fixture.source, change: 'deleted', sha256: null,
        git_blob_sha: git('rev-parse', fixture.base + ':' + fixture.source)},
    ];
    const patch = execFileSync('git', ['diff', '--binary', fixture.base, proof.commit, '--'], {cwd: directory});
    options = input({proof, patch: transportFile('add-delete.patch', patch), bundle: bundleFor(proof.commit)});
  } finally { git('switch', fixture.branch); }
  assert.equal(guard.verifyPublication(options).status, 'PASS');
});

test('selected target scope cannot expand to another controller-allowed component', () => {
  const proof = clone(fixture.proof);
  proof.binding.allowed_paths.push('msw-ai-centrum-robotow/app/');
  proof.pr_binding_block = guard.bindingBlock(proof.binding);
  blocked(input({proof}), 'invalid_proof_scope');
  proof.binding.allowed_paths = ['msw-ai-technical-core/'];
  proof.pr_binding_block = guard.bindingBlock(proof.binding);
  proof.changes[0].path = 'msw-ai-centrum-robotow/app/api.py';
  blocked(input({proof}), 'invalid_proof_change');
});

test('requires both transport files, rejecting outside paths, symlinks and hardlinks', () => {
  blocked(input({resolver: () => ({patchPath: fixture.patch})}), 'missing_transport_files');
  const outside = path.join(directory, 'outside.patch'); fs.writeFileSync(outside, 'outside');
  blocked(input({patch: outside}), 'invalid_transport_path');
  const link = path.join('/tmp/gh-aw', prefix + '-symlink'); transportFiles.push(link);
  fs.symlinkSync(fixture.patch, link);
  blocked(input({patch: link}), 'invalid_transport_file');
  const source = transportFile('hardlink-source', 'source');
  const hardlink = path.join('/tmp/gh-aw', prefix + '-hardlink'); transportFiles.push(hardlink);
  fs.linkSync(source, hardlink);
  blocked(input({patch: hardlink}), 'invalid_transport_file');
});

test('bounds patch and bundle bytes and rejects non-bundle content', () => {
  const tooLargePatch = transportFile('large.patch', Buffer.alloc(guard.MAX_PATCH_BYTES + 1, 65));
  blocked(input({patch: tooLargePatch}), 'file_size_limit');
  const largeBundle = transportFile('large.bundle', 'x');
  fs.truncateSync(largeBundle, guard.MAX_BUNDLE_BYTES + 1);
  blocked(input({bundle: largeBundle}), 'file_size_limit');
  blocked(input({bundle: transportFile('bad.bundle', 'not a git bundle')}), 'git_verification_failed');
});

test('CLI fails closed with a fixed public reason before opening missing input files', () => {
  const result = spawnSync(process.execPath, [path.resolve(__dirname, '../task_publication.cjs'),
    '--proof', '/missing/proof.json'], {encoding: 'utf8', env: {
    PATH: process.env.PATH, MSW_CODING_EXECUTION_PERMITTED: 'false',
    GH_AW_AGENT_OUTPUT: '/missing/output.json',
  }});
  assert.equal(result.status, 1);
  const receipt = JSON.parse(result.stdout);
  assert.equal(receipt.reason, 'execution_not_permitted');
  assert.equal(receipt.publication_performed, false);
  assert.equal(result.stderr, '');
});
