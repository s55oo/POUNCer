// Run: node tests/browser_contest_profiles.cjs
// Exercise actual status rendering and tile-class expressions without a radio.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('pouncer/web.py', 'utf8').replace(/\r\n/g, '\n');
const start = source.indexOf("    const sessionEl = document.getElementById('session');");
const status = source.slice(start, source.indexOf('    renderFkeys', start));
assert.ok(start > 0);
const el = {};
const context = vm.createContext({document: {getElementById(id) {
  assert.equal(id, 'session'); return el;
}}, data: {session: 'TTC-SPCWC 2026-11-10 19:30 UTC · period 2/4 · 15:00 to next period'}});
vm.runInContext('{\n' + status + '\n}', context);
assert.equal(el.hidden, false);
assert.equal(el.textContent, context.data.session);
context.data.session = '';
vm.runInContext('{\n' + status + '\n}', context);
assert.equal(el.hidden, true);
const clsStart = source.indexOf("      const cls = 'tile'");
const expression = source.slice(clsStart, source.indexOf('      // DX', clsStart));
const tile = vm.createContext({s: {call: 'S53ZO', worked: true, snr: 22, mult: 0},
  snrVal: s => s.snr, tuned: null, data: {}, spotKey: s => s.call, selKey: '', calm: false});
function classes() { return vm.runInContext('(() => {' + expression + '; return cls; })()', tile); }
assert.ok(classes().includes(' worked'));
tile.s.worked = false;  // period reset, same tile/callsign
assert.ok(!classes().includes(' worked'));
assert.ok(classes().includes(' strong'));
assert.ok(source.includes('button.tile.worked .call { color: #9e9e9e; }'));
console.log('Browser profiles: session status visibility and worked class reset passed');
