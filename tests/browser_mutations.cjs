// Run actual production mutation helper against harmless HTTP response doubles.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('pouncer/web.py', 'utf8');
const helper = source.split('MUTATION_CLIENT_JS = """')[1].split('"""')[0];
const response = (status, data) => ({status, ok: status >= 200 && status < 300,
  json: async () => data, clone: () => response(status, data)});
function client(replies) {
  const calls=[];
  const c=vm.createContext({fetch:async (url, options) => {
    calls.push({url, options});
    const next=replies.shift();
    assert.ok(next, 'unexpected extra network request');
    if(next instanceof Error)throw next;
    return next;
  }});
  vm.runInContext(helper, c);
  return {calls, send:url=>c.mutationFetch(url)};
}
(async()=>{
  const c=client([response(200,{token:'first'}),response(200,{ok:true}),response(200,{ok:true})]);
  await c.send('/api/key?key=F1');await c.send('/api/settings/set?key=x&value=y');
  assert.deepEqual(c.calls.map(x=>x.url),['/api/session','/api/key?key=F1','/api/settings/set?key=x&value=y']);
  for(const {options} of c.calls.slice(1)) {
    assert.equal(options.method,'POST');assert.equal(options.headers['X-POUNCer-Token'],'first');
    assert.equal(options.credentials,'same-origin');
  }
  const restarted=client([response(200,{token:'old'}),response(403,{error_code:'csrf'}),
    response(200,{token:'new'}),response(200,{ok:true})]);
  await restarted.send('/api/key?key=F2');
  assert.equal(restarted.calls[1].options.headers['X-POUNCer-Token'],'old');
  assert.equal(restarted.calls[3].options.headers['X-POUNCer-Token'],'new');
  assert.equal(restarted.calls.filter(x=>x.url==='/api/session').length,2);
  for(const status of [403,500]) {
    const denied=client([response(200,{token:'ok'}),response(status,{error:'not executed or uncertain'})]);
    assert.equal((await denied.send('/api/key?key=F3')).status,status);
    assert.equal(denied.calls.length,2,'never retry a permission failure or uncertain action');
  }
  const failed=client([response(500,{}),response(200,{token:'recovered'}),response(200,{ok:true})]);
  await assert.rejects(failed.send('/api/key?key=F4'));
  await failed.send('/api/key?key=F4');
  assert.equal(failed.calls.length,3,'failed token lookup can recover');
  const lost=client([response(200,{token:'ok'}),new Error('network dropped after sending')]);
  await assert.rejects(lost.send('/api/key?key=F5'));
  assert.equal(lost.calls.length,2,'never retry a possibly delivered transmission');
  console.log('Browser mutation handshake, POST, token renewal and no duplicate controls passed');
})().catch(e=>{console.error(e);process.exitCode=1});
