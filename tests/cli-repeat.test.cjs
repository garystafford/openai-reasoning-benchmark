const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const http=require('node:http');
const {execFile}=require('node:child_process');
const {promisify}=require('node:util');
const exec=promisify(execFile);
const {schemaHash}=require('../evals/output-schema.cjs');

test('CLI repeats through the file provider make fresh calls across CJS/ESM cache boundaries',async()=>{
  const folder=fs.mkdtempSync(path.join(os.tmpdir(),'benchmark-cli-repeat-'));
  let requests=0;
  const server=http.createServer(async(req,res)=>{
    for await(const chunk of req) {};
    const id=++requests;
    res.writeHead(200,{'content-type':'application/json'});
    res.end(JSON.stringify({id:`resp_fixture_${id}`,model:'gpt-6-sol',status:'completed',
      output:[{type:'message',role:'assistant',content:[{type:'output_text',text:'fixture'}]}],
      usage:{input_tokens:10,output_tokens:5,total_tokens:15}}));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  try {
    const format={type:'json_schema',name:'fixture',strict:true,schema:{type:'object',properties:{},required:[],additionalProperties:false}};
    const config={prompts:['[{"role":"user","content":"Same request"}]'],
      providers:[{id:`file://${path.resolve(__dirname,'../evals/provider.cjs')}`,label:'fixture',config:{
        model:'gpt-6-sol',reasoning:{effort:'low'},apiKey:'fixture-key',apiBaseUrl:`http://127.0.0.1:${server.address().port}/v1`}}],
      tests:[{metadata:{output_format:format,output_schema_sha256:schemaHash(format)},
        assert:[{type:'equals',value:'fixture'}]}],evaluateOptions:{repeat:3,maxConcurrency:1}};
    const configPath=path.join(folder,'config.json');fs.writeFileSync(configPath,JSON.stringify(config));
    const cli=path.join(path.dirname(require.resolve('promptfoo')),'entrypoint.js');
    const ids=[];
    // Enable caching at process startup to prove the adapter disables its own module's cache.
    const env={...process.env,PROMPTFOO_CONFIG_DIR:folder,PROMPTFOO_CACHE_ENABLED:'true',
      PROMPTFOO_DISABLE_TELEMETRY:'1',PROMPTFOO_DISABLE_UPDATE:'1',OPENAI_API_KEY:'fixture-key'};
    for(let run=0;run<2;run++) {
      const output=path.join(folder,`run-${run}.json`);
      await exec(process.execPath,[cli,'eval','-c',configPath,'--no-cache','-o',output],{env,timeout:60000,maxBuffer:2**20});
      const result=JSON.parse(fs.readFileSync(output));
      assert.equal(result.results.results.length,3);
      for(const r of result.results.results) {
        assert.equal(r.response.cached,false,'No repeated or subsequent run may reuse a response');
        ids.push(r.response.raw.id);
      }
    }
    assert.equal(requests,6);
    assert.equal(new Set(ids).size,6);
  } finally {
    await new Promise(resolve=>server.close(resolve));
    fs.rmSync(folder,{recursive:true,force:true});
  }
});
