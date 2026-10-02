#!/usr/bin/env node
// Keep orchestration, assertions, retries, persistence, and viewing in Promptfoo.
// This launcher only selects credentials, verifies fixtures, and sets local defaults.
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync, spawn } = require('node:child_process');
const { buildConfig, selectModels, ROOT } = require('../evals/config.cjs');
const { models } = require('../evals/models.json');

function credentialEnvironment(env, explicitFile, root = ROOT) {
  const localPath = path.join(root, '.benchmark.local.json');
  const local = fs.existsSync(localPath) ? JSON.parse(fs.readFileSync(localPath, 'utf8')) : {};
  const selectedFile = explicitFile || env.BENCHMARK_ENV_FILE || local.envFile;
  let key = env.OPENAI_API_KEY?.trim();
  // An explicitly selected source takes precedence over inherited credentials.
  if (selectedFile || !key) {
    const file = path.resolve(root, selectedFile || '.env.local');
    if (!fs.existsSync(file)) throw new Error('Credential file is missing; use --env-file or OPENAI_API_KEY.');
    const parsed = require('dotenv').parse(fs.readFileSync(file));
    key = parsed.OPENAI_API_KEY?.trim();
  }
  if (!key) throw new Error('OPENAI_API_KEY is missing or blank in the selected source.');
  // Never import other credentials or endpoint overrides from the shared file.
  return { ...env, OPENAI_API_KEY: key };
}

function main(argv = process.argv.slice(2)) {
  const [command = 'plan', ...rest] = argv;
  const args = [...rest];
  let envFile;
  const index = args.indexOf('--env-file');
  if (index >= 0) {
    if (!args[index + 1] || args[index + 1].startsWith('-')) throw new Error('--env-file needs a path.');
    envFile = args[index + 1];
    args.splice(index, 2);
  }
  let env = {
    ...process.env,
    PROMPTFOO_CONFIG_DIR: path.join(ROOT, '.promptfoo'),
    PROMPTFOO_DISABLE_TELEMETRY: '1',
    PROMPTFOO_DISABLE_UPDATE: '1',
    PROMPTFOO_CACHE_ENABLED: 'false',
    PROMPTFOO_PYTHON: process.env.PROMPTFOO_PYTHON || 'python3',
    PYTHONPATH: [ROOT, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
  };
  if (command === 'matrix' || command === 'matrix-plan') {
    env = { ...env,
      ...(!env.BENCHMARK_MODELS && !env.BENCHMARK_FAMILY ? { BENCHMARK_FAMILY: 'gpt-6' } : {}),
      BENCHMARK_EFFORT: env.BENCHMARK_EFFORT || 'all', BENCHMARK_RUNS: env.BENCHMARK_RUNS || '3',
      BENCHMARK_MAX_OUTPUT_TOKENS: env.BENCHMARK_MAX_OUTPUT_TOKENS || '16384' };
  }
  if (!['plan', 'eval', 'view', 'matrix', 'matrix-plan'].includes(command)) throw new Error('Invalid evaluation command.');
  if (command !== 'view') {
    const config = buildConfig(env);
    const selected = selectModels(env);
    const efforts = env.BENCHMARK_EFFORT === 'all' || (!env.BENCHMARK_EFFORT && env.BENCHMARK_FAMILY) ? require('../evals/config.cjs').EFFORTS
      : (env.BENCHMARK_EFFORT || 'low,medium,high').split(',').map((v) => v.trim());
    const scenarios = config.tests.length;
    console.log(JSON.stringify({
      scenarios, providers: config.providers.map((p) => p.label),
      outputMode: 'structured_outputs', strictSchema: true,
      modelFamily: env.BENCHMARK_FAMILY || null,
      collection: config.tests[0].metadata.evaluation_collection,
      domains: config.tests.slice(0, scenarios).reduce((counts,t) => { counts[t.metadata.domain] = (counts[t.metadata.domain] || 0) + 1; return counts; }, {}),
      distinctTaskFamilies: new Set(config.tests.slice(0, scenarios).map(t => t.metadata.family_id)).size,
      variantCounts: config.tests.slice(0, scenarios).reduce((counts, t) => {
        const kind = t.metadata.variant_kind || t.metadata.case_set;
        counts[kind] = (counts[kind] || 0) + 1; return counts;
      }, {}),
      profile: env.BENCHMARK_PROFILE || 'all', caseSet: env.BENCHMARK_CASE_SET || 'core',
      track: env.BENCHMARK_TRACK || 'decision',
      difficulty: env.BENCHMARK_DIFFICULTY || (env.BENCHMARK_TRACK && env.BENCHMARK_TRACK !== 'decision' ? 'all' : 'complex'),
      split: env.BENCHMARK_SPLIT || (env.BENCHMARK_TRACK && env.BENCHMARK_TRACK !== 'decision' ? 'all' : 'development'),
      datasetVersions: [...new Set(config.tests.slice(0, scenarios).map(t => t.metadata.dataset_version))],
      suites: [...new Set(config.tests.map((t) => t.vars.suite))],
      repetitions: config.evaluateOptions.repeat,
      plannedRequests: scenarios * config.providers.length * config.evaluateOptions.repeat,
      skipped: selected.flatMap((model) => efforts.filter((e) => !models[model].efforts.includes(e)).map((e) => `${model} / ${e}`)),
    }, null, 2));
    if (['plan', 'matrix-plan'].includes(command)) return;
    env = credentialEnvironment(env, envFile);
    for (const suite of new Set(config.tests.map((t) => t.vars.suite))) {
      const verified = spawnSync(env.PROMPTFOO_PYTHON, ['-m', `benchmarks.${suite}.verify_answer_key`], { cwd: ROOT, env, stdio: 'inherit' });
      if (verified.error || verified.status !== 0) throw new Error(`Answer-key verification failed: ${suite}`);
    }
  }
  fs.mkdirSync(path.join(ROOT, '.promptfoo'), { recursive: true });
  const cli = path.join(path.dirname(require.resolve('promptfoo')), 'entrypoint.js');
  let cliArgs;
  if (command === 'view') {
    cliArgs = ['view', '--no', ...args];
  } else {
    fs.mkdirSync(path.join(ROOT, 'results'), { recursive: true });
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    cliArgs = ['eval', '-c', 'promptfooconfig.cjs', '--no-cache',
      '-o', `results/promptfoo-${stamp}.json`,
      ...args];
  }
  const child = spawn(process.execPath, [cli, ...cliArgs], { cwd: ROOT, env, stdio: 'inherit' });
  for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
  child.on('error', () => { console.error('Unable to start Promptfoo. Run npm ci.'); process.exitCode = 1; });
  child.on('exit', (code, signal) => { process.exitCode = code ?? (signal === 'SIGINT' ? 130 : 1); });
}

module.exports = { credentialEnvironment, main };
if (require.main === module) {
  try { main(); } catch (error) { console.error(error.message); process.exitCode = 1; }
}
