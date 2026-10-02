// Compile the prompt's declared output contract, never the golden answer.
// Strict Structured Outputs disallows open dictionaries. Their possible keys
// come from supplied input records; optional maps use explicit object branches.
const crypto = require('node:crypto');

const scalar = (type) => ({ type });
const object = (properties) => ({ type: 'object', properties,
  required: Object.keys(properties), additionalProperties: false });
const map = (keys, value) => object(Object.fromEntries(keys.map(k => [k, value])));
const nullable = (schema) => ({ anyOf: [schema, scalar('null')] });
const emptyOr = (schema) => ({ anyOf: [object({}), schema] });
const strings = () => ({ type: 'array', items: scalar('string') });
function subsets(keys, value) {
  if (keys.length > 8) throw new Error('Optional dictionary exceeds schema branch budget');
  return { anyOf: Array.from({ length: 2 ** keys.length }, (_, mask) =>
    map(keys.filter((_, i) => mask & (1 << i)), value)) };
}

function dictionary(suite, scenario, field, prompt) {
  // SOURCE PACK is JSON supplied to the model, without expected answers.
  const start = prompt.indexOf('\nSOURCE PACK\n');
  const docs = start < 0 ? [] : JSON.parse(prompt.slice(start + '\nSOURCE PACK\n'.length).split('\n\nTASK\n')[0]);
  const data = id => {
    const doc = docs.find(d => d.id === id);
    if (!doc?.data) throw new Error(`Missing input document ${id}`);
    return doc.data;
  };
  const ids = (doc, field) => data(doc)[field].map(x => x.id);
  const base = scenario;
  const integer = scalar('integer');
  const string = scalar('string');
  if (suite === 'complex') {
    if (base === 'liquidity_facilities' && field === 'ending_free_cash') return nullable(map(['1','2','3'], integer));
    if (base === 'engagement_capacity') {
      if (field === 'assignments') return emptyOr(map(ids('WORKPLAN','roles'), string));
      if (field === 'weekly_cost_dollars') return nullable(map(['1','2','3'], integer));
    }
    if (base === 'recovery_chain' && field === 'start_minutes') return emptyOr(map(['RESTORE', ...ids('RUNBOOK','jobs')], integer));
  }
  if (suite === 'reasoning') {
    if (base === 'production_jobshop') return map(ids('LOTS','lots'), integer);
    if (base === 'warehouse_queue') return map(ids('ORDERS','orders'), integer);
    if (base === 'contingent_supplier') return map(data('SCENARIOS').names, integer);
    if (base === 'invoice_match') return map(ids('PO','lines'), field === 'reasons' ? string : integer);
    if (base === 'treasury_netting') {
      const keys = field === 'positions' ? Object.entries(data('NETTING-AGREEMENT').pools)
        .flatMap(([currency, entities]) => entities.map(entity => `${entity}:${currency}`)) : Object.keys(data('CASH').free);
      return map(keys, integer);
    }
    if (base === 'rollout_impact_estimate') return map([...new Set(data('CELLS').rows.map(r => `${r.group}:${r.period}`))], integer);
    if (base === 'workpackage_assignment') return field === 'assignments'
      ? map(ids('PACKAGES','packages'), string) : map(ids('PEOPLE','people'), integer);
    if (base === 'fault_hypothesis_constraints' && field === 'minimal_explanations') {
      // A minimum-cardinality set can have at most C(n, floor(n/2)) explanations.
      // Allow every count, without using probes to infer the correct count.
      const n = data('TOPOLOGY').components.length;
      if (n > 6) throw new Error('Explanation dictionary exceeds schema branch budget');
      let maximum = 1;
      for (let i = 1; i <= Math.floor(n / 2); i++) maximum = maximum * (n - i + 1) / i;
      return { anyOf: Array.from({ length: maximum + 1 }, (_, count) =>
        map(Array.from({ length: count }, (_, i) => `E${i + 1}`), strings())) };
    }
    if (base === 'consistent_restore_cut') return map(Object.keys(data('CHECKPOINTS')), string);
    if (base === 'regional_failover') return map(data('FAILURE-MODEL').domains, integer);
    if (base === 'expand_contract_rollout' && field === 'final_state') return object(Object.fromEntries(
      Object.entries(data('MIGRATION').initial).map(([k,v]) => [k, scalar(typeof v === 'boolean' ? 'boolean' : 'string')])));
    if (base === 'version_contract_resolution') return map(Object.keys(data('VERSIONS')), string);
  }
  throw new Error(`No dictionary contract for ${suite}/${scenario}.${field}`);
}

function outputFormat(suite, scenario, prompt) {
  const tail = prompt.slice(prompt.lastIndexOf('\n\n') + 2);
  const offset = tail.indexOf('{');
  if (offset < 0) throw new Error(`Missing output contract: ${suite}/${scenario}`);
  const template = JSON.parse(tail.slice(offset)
    .replace(/<integer>/g, '"integer"').replace(/<number>/g, '"number"')
    .replace(/<true or false>/g, '"boolean"'));
  function compile(value, field) {
    if (Array.isArray(value)) {
      const integerItems = value[0] === 'three integers';
      return { type: 'array', items: scalar(integerItems ? 'integer' : 'string'),
        ...(integerItems ? { minItems: 3, maxItems: 3 } : {}) };
    }
    if (value && typeof value === 'object') {
      if (Object.keys(value).some(k => k.startsWith('<'))) return dictionary(suite, scenario, field, prompt);
      return object(Object.fromEntries(Object.entries(value).map(([k,v]) => [k, compile(v,k)])));
    }
    if (typeof value !== 'string') throw new Error('Output contracts must declare types, not values');
    if (value.startsWith('dictionary')) return dictionary(suite, scenario, field, prompt);
    if (['integer','number','boolean','null'].includes(value)) return scalar(value);
    if (value.endsWith(' or null')) return nullable(scalar(value.startsWith('integer') ? 'integer' : value.startsWith('number') ? 'number' : 'string'));
    const choices = value.replace(/^exactly one action code: /, '').split(';')[0];
    if (choices.includes(',') || choices.includes(' or ')) return { type: 'string',
      enum: choices.split(/,\s*(?:or\s+)?|\s+or\s+/).map(s => s.trim()) };
    const labels = ['ID','center','classification','action','original key','rule ID','runbook action','YYYY-MM-DD','HH:MM'];
    if (labels.includes(value) || /^<[^>]+>$/.test(value)) return scalar('string');
    throw new Error(`Unknown output type ${value}: ${suite}/${scenario}.${field}`);
  }
  const schema = compile(template, 'root');
  return { type: 'json_schema', name: `${suite}_${scenario}`, strict: true, schema };
}

const schemaHash = format => crypto.createHash('sha256').update(JSON.stringify(format)).digest('hex');
module.exports = { outputFormat, schemaHash };
