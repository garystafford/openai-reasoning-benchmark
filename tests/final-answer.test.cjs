const {test}=require('node:test');
const assert=require('node:assert/strict');
const {finalAnswer,POLICY}=require('../evals/final-answer.cjs');
const message=(text,phase)=>({type:'message',role:'assistant',...(phase?{phase}:{}),
  content:[{type:'output_text',text}]});
const response=(...output)=>({output:'native concatenation',raw:{status:'completed',output}});

test('explicit final answer excludes commentary and preserves raw data',()=>{
  const r=response(message('Prose and {"wrong":true}','commentary'),message('{"answer":42}','final_answer'));
  const original=JSON.stringify(r);
  const selected=finalAnswer(r);
  assert.equal(selected.output,'{"answer":42}');
  assert.equal(selected.metadata.policy,POLICY);
  assert.equal(selected.metadata.commentaryMessages,1);
  assert.equal(JSON.stringify(r),original);
});

test('legacy single unphased message works without extracting or repairing JSON',()=>{
  assert.equal(finalAnswer(response(message('bad JSON'))).output,'bad JSON');
  assert.equal(finalAnswer(response(message('{"x":1}'))).metadata.selection,'single_unphased_message');
  assert.equal(finalAnswer(response(message('commentary','commentary'))).output,'');
});

test('missing or multiple finals fail closed rather than scoring arbitrary text',()=>{
  for(const r of [response(),response(message('{}','commentary')),
    response(message('{}','final_answer'),message('{}','final_answer')),
    response(message('{}'),message('{}')),
    response({type:'message',role:'user',phase:'final_answer',content:[{type:'output_text',text:'{}'}]})]) {
    const selected=finalAnswer(r);assert.ok(selected.error);assert.equal(selected.output,'');
  }
  assert.match(finalAnswer(response({type:'message',role:'assistant',phase:'final_answer',content:[]})).error,/no output text/);
});

test('API errors, refusals and partial final output retain their original status',()=>{
  const error={error:'HTTP fixture error',output:undefined};assert.equal(finalAnswer(error).metadata.selection,'provider_error');
  const refused=response({type:'message',role:'assistant',phase:'final_answer',content:[{type:'refusal',refusal:'fixture'}]});
  assert.equal(finalAnswer(refused).metadata.selection,'refusal');assert.equal(finalAnswer(refused).output,refused.output);
  const partial=response(message('{"answer":','final_answer'));partial.raw.status='incomplete';
  assert.equal(finalAnswer(partial).output,'{"answer":');assert.equal(partial.raw.status,'incomplete');
});
