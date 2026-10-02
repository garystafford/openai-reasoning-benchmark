const {test}=require('node:test');
const assert=require('node:assert/strict');
const Ajv=require('ajv');
const fs=require('node:fs');
const {buildConfig,ROOT}=require('../evals/config.cjs');
const {credentialEnvironment}=require('../scripts/promptfoo.cjs');
test('post matrix contains 27 tasks and 17 GPT-6 configurations',()=>{
 const c=buildConfig({BENCHMARK_FAMILY:'gpt-6',BENCHMARK_RUNS:'3'});
 assert.equal(c.tests.length,27); assert.equal(c.providers.length,17); assert.equal(c.evaluateOptions.repeat,3);
 assert.equal(new Set(c.tests.map(t=>t.metadata.domain)).size,9);
});
test('GPT-5.6 comparison supports low and high on the same tasks',()=>{
 const c=buildConfig({BENCHMARK_MODELS:'gpt-5.6-sol',BENCHMARK_EFFORT:'low,high'});
 assert.equal(c.tests.length,27);assert.equal(c.providers.length,2);
});
test('all main goldens satisfy their strict output schemas',()=>{
 const ajv=new Ajv({strict:false});
 for(const t of buildConfig({}).tests){
  const key=JSON.parse(fs.readFileSync(`${ROOT}/benchmarks/${t.vars.suite}/expected_answers.json`));
  const f=t.metadata.output_format;
  assert.equal(f.strict,true);assert.ok(ajv.validate(f.schema,key.answers[t.vars.scenario]),JSON.stringify(ajv.errors));
 }
});
test('explicit credential file overrides inherited key without importing other variables',()=>{
 const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'public-benchmark-key-'));
 try{fs.writeFileSync(path.join(dir,'.env.local'),'OPENAI_API_KEY=test-key\nUNRELATED_SECRET=other');
 const e=credentialEnvironment({OPENAI_API_KEY:'old'},'.env.local',dir);
 assert.equal(e.OPENAI_API_KEY,'test-key');assert.equal(e.UNRELATED_SECRET,undefined);
 }finally{fs.rmSync(dir,{recursive:true,force:true});}
});
