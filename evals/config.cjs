const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { models, defaultModels, asOf, source } = require('./models.json');
const suites = require('./suites.json');
const catalog = require('./benchmark-catalog.json');
const mainMatrix = require('./main-matrix.json');
const { outputFormat, schemaHash } = require('./output-schema.cjs');
const { POLICY: outputExtraction } = require('./final-answer.cjs');
const mainCases = new Set(mainMatrix.cases.map(c => c.case_id));
const ROOT = path.resolve(__dirname, '..');
const EFFORTS = ['none', 'low', 'medium', 'high', 'xhigh', 'max'];

function selection(value, defaults, allowed, name) {
  const selected = value === undefined ? defaults : value.split(',').map((v) => v.trim());
  if (!selected.length || new Set(selected).size !== selected.length ||
      selected.some((v) => !allowed.includes(v))) {
    throw new Error(`${name} must contain unique values from: ${allowed.join(', ')}`);
  }
  return selected;
}

function integer(value, fallback, min, max, name) {
  if (value === undefined) return fallback;
  if (!/^\d+$/.test(value) || Number(value) < min || Number(value) > max) {
    throw new Error(`${name} must be an integer between ${min} and ${max}`);
  }
  return Number(value);
}

function hash(value) {
  return crypto.createHash('sha256').update(value).digest('hex');
}

function selectModels(env) {
  const family = env.BENCHMARK_FAMILY;
  if (family !== undefined && !['gpt-6', 'gpt-5.6'].includes(family)) throw new Error('BENCHMARK_FAMILY must be gpt-6 or gpt-5.6');
  if (family !== undefined && env.BENCHMARK_MODELS !== undefined) throw new Error('Choose BENCHMARK_FAMILY or BENCHMARK_MODELS, not both');
  return selection(env.BENCHMARK_MODELS, family ? Object.keys(models).filter(m => m.startsWith(family + '-')) : defaultModels, Object.keys(models), 'BENCHMARK_MODELS');
}

function buildConfig(env = process.env) {
  const selectedModels = selectModels(env);
  const effortSetting = env.BENCHMARK_EFFORT ?? (env.BENCHMARK_FAMILY ? 'all' : undefined);
  const efforts = selection(effortSetting === 'all' ? undefined : effortSetting,
    effortSetting === 'all' ? EFFORTS : ['low', 'medium', 'high'], EFFORTS, 'BENCHMARK_EFFORT');
  const track = env.BENCHMARK_TRACK ?? 'decision';
  if (!['decision'].includes(track)) throw new Error('Invalid BENCHMARK_TRACK');
  const defaultSuites = Object.keys(suites).filter((s) => track === 'decision' ? ['decision','complex','reasoning'].includes(s)
    : track === 'all' ? true : suites[s].default);
  const selectedSuites = selection(env.BENCHMARK_SUITES, defaultSuites, Object.keys(suites), 'BENCHMARK_SUITES');
  const profile = env.BENCHMARK_PROFILE ?? 'all';
  if (!['all', ...Object.keys(catalog.profiles)].includes(profile)) throw new Error('Invalid BENCHMARK_PROFILE');
  const caseSet = env.BENCHMARK_CASE_SET ?? 'core';
  if (!['core'].includes(caseSet)) throw new Error('Invalid BENCHMARK_CASE_SET');
  const difficulty = env.BENCHMARK_DIFFICULTY === 'all' ? ['simple','moderate','complex']
    : selection(env.BENCHMARK_DIFFICULTY, track === 'decision' ? ['complex'] : ['simple','moderate','complex'],
      ['simple','moderate','complex'], 'BENCHMARK_DIFFICULTY');
  const split = env.BENCHMARK_SPLIT ?? (track === 'decision' ? 'development' : 'all');
  if (!['development'].includes(split)) throw new Error('Invalid BENCHMARK_SPLIT');
  const collection = env.BENCHMARK_COLLECTION ?? (track === 'decision' && split === 'development' ? 'main' : 'all');
  if (!['main'].includes(collection)) throw new Error('Invalid BENCHMARK_COLLECTION');
  const domains = [...new Set(mainMatrix.cases.map(c => c.domain))];
  const selectedDomains = env.BENCHMARK_DOMAIN ? selection(env.BENCHMARK_DOMAIN, domains, domains, 'BENCHMARK_DOMAIN') : domains;
  const repeat = integer(env.BENCHMARK_RUNS, 1, 1, 100, 'BENCHMARK_RUNS');
  const limit = integer(env.BENCHMARK_MAX_OUTPUT_TOKENS, track === 'decision' ? 16384 : undefined, 16, 128000, 'BENCHMARK_MAX_OUTPUT_TOKENS');
  const concurrency = integer(env.BENCHMARK_CONCURRENCY, 1, 1, 32, 'BENCHMARK_CONCURRENCY');
  const verbosity = env.BENCHMARK_VERBOSITY ?? 'default';
  if (!['default', 'low', 'medium', 'high'].includes(verbosity)) throw new Error('Invalid BENCHMARK_VERBOSITY');

  const providers = selectedModels.flatMap((model) => efforts
    .filter((effort) => models[model].efforts.includes(effort))
    .map((effort) => ({
      id: 'file://evals/provider.cjs',
      label: `${model} / ${effort}`,
      config: {
        model,
        apiBaseUrl: 'https://api.openai.com/v1',
        reasoning: { effort },
        store: false,
        // Omit sampling defaults so every effort uses the same API defaults.
        omitDefaults: true,
        passthrough: { service_tier: 'default' },
        ...(verbosity !== 'default' ? { verbosity } : {}),
        ...(limit !== undefined ? { max_output_tokens: limit } : {}),
      },
    })));
  if (!providers.length) throw new Error('No selected model supports the selected reasoning efforts');

  const tests = selectedSuites.flatMap((suite) => {
    const directory = path.join(ROOT, 'benchmarks', suite);
    const promptsRaw = fs.readFileSync(path.join(directory, 'prompts.json'), 'utf8');
    const answersRaw = fs.readFileSync(path.join(directory, 'expected_answers.json'), 'utf8');
    const prompts = JSON.parse(promptsRaw);
    const answers = JSON.parse(answersRaw);
    const ids = prompts.scenarios?.map((s) => s.id) ?? [];
    if (prompts.version !== 2 || answers.version !== 2 || !ids.length ||
        new Set(ids).size !== ids.length ||
        JSON.stringify([...ids].sort()) !== JSON.stringify(Object.keys(answers.answers ?? {}).sort())) {
      throw new Error(`Invalid prompt/answer coverage for ${suite}`);
    }
    return prompts.scenarios.flatMap((scenario) => {
      if (!scenario.id || !scenario.difficulty || typeof scenario.prompt !== 'string' || !scenario.prompt.trim()) {
        throw new Error(`Invalid scenario in ${suite}`);
      }
      const metadata = catalog.cases[`${suite}/${scenario.id}`];
      if (!metadata || metadata.prompt_sha256 !== hash(scenario.prompt)) throw new Error(`Missing or stale benchmark metadata: ${suite}/${scenario.id}`);
      if (track !== 'all' && (metadata.track || 'sanity') !== track) return [];
      if (!difficulty.includes(scenario.difficulty)) return [];
      if (collection === 'main' && !mainCases.has(`${suite}/${scenario.id}`)) return [];
      if (env.BENCHMARK_DOMAIN && !selectedDomains.includes(metadata.domain)) return [];
      if (metadata.track === 'decision' && split !== 'all' && (metadata.evaluation_split || 'development') !== split) return [];
      if ((profile !== 'all' && !metadata.customer_profiles.includes(profile)) ||
          (caseSet !== 'all' && metadata.case_set !== caseSet)) return [];
      const format = outputFormat(suite, scenario.id, scenario.prompt);
      return [{
        description: `${suite}/${scenario.id} (${scenario.difficulty}; ${metadata.customer_profiles.join(', ') || 'main'})`,
        vars: { suite, scenario: scenario.id, question: scenario.prompt, system: suites[suite].systemPrompt },
        metadata: {
          ...metadata, evaluation_collection: collection, main_matrix_version: collection === 'main' ? mainMatrix.version : null, track: metadata.track || 'sanity', dataset_version: metadata.dataset_version || catalog.version,
          run_conditions: { repeat, concurrency, max_output_tokens: limit ?? null, verbosity,
            service_tier: 'default', response_cache: false, output_mode: 'structured_outputs',
            output_extraction: outputExtraction,
            schema_compiler_sha256: hash(fs.readFileSync(path.join(ROOT, 'evals/output-schema.cjs'))) },
          output_format: format, output_schema_sha256: schemaHash(format),
          grader_sha256: hash(['evals/grade.py', 'evals/decision_grade.py', 'evals/decision_constraints.py',
            'benchmarks/decision/reference.py',
            'benchmarks/complex/reference.py', 'benchmarks/reasoning/reference.py']
            .map(file => fs.readFileSync(path.join(ROOT, file), 'utf8')).join('\n')),
          suite, scenario: scenario.id, difficulty: scenario.difficulty,
          variant: suites[suite].variant,
          prompt_suite_sha256: hash(promptsRaw), answer_key_sha256: hash(answersRaw),
          system_prompt_sha256: hash(suites[suite].systemPrompt),
          model_catalog_as_of: asOf, model_catalog_source: source,
        },
      }];
    });
  });
  if (!tests.length) throw new Error('No cases match the selected profile, suites, and case set');
  return {
    description: `Customer reasoning benchmark — ${track} / ${profile} / ${caseSet}`,
    prompts: ['file://evals/prompt.cjs'],
    providers,
    tests,
    defaultTest: {
      // Preserve the grader's named scores: an assertion metric with the same
      // name would replace strict correctness with the decision task score.
      assert: [{ type: 'python', value: 'file://evals/grade.py' }],
    },
    evaluateOptions: { maxConcurrency: concurrency, repeat },
  };
}

module.exports = { buildConfig, selectModels, EFFORTS, ROOT };
