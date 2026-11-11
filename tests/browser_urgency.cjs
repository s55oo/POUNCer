// Run: node tests/browser_urgency.cjs
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('pouncer/web.py', 'utf8').replace(/\r\n/g, '\n');
const policy = source.slice(source.indexOf('  function isRare(s)'), source.indexOf('  // Alert:'));
const c = vm.createContext({lastData: {rare_hours: 6}});
vm.runInContext(policy, c);
assert.equal(c.isRare({rare: false, run: 80, hours: 0.8}), false);
assert.equal(c.isRare({rare: true, run: 80, hours: 0.2}), true);
assert.equal(c.isRare({run: 5, hours: 0.1}), false); // no unsafe legacy inference
const flagged = {rare: true, run: 80, hours_fraction: 0.2, rare_hours_limit: 0.25};
const ordinary = {rare: false, run: 80, hours_fraction: 0.8, rare_hours_limit: 0.25};
assert.ok(c.rareKey(flagged) < c.rareKey(ordinary));
assert.equal(c.rareKey({run: null, hours: 0.1, rare_hours_limit: null}), 1e6);
assert.ok(c.rareKey({rare: true, run: 10}) < c.rareKey(flagged));
const alerts = source.slice(source.indexOf('  function checkAlerts(spots)'), source.indexOf('  // Ping:'));
Object.assign(c, {alerted: new Set(), alertsReady: false, alertPop: true, alertSound: false,
                 bandOf: () => '40m', toast: text => c.messages.push(text), messages: []});
vm.runInContext(alerts, c);
c.checkAlerts([]);
c.checkAlerts([{call:'FULL', freq:7020, mult:1, rare:false, run:80, hours:0.8}]);
assert.equal(c.messages.length, 0);
c.checkAlerts([{call:'BRIEF', freq:7021, mult:1, rare:true, run:80, hours:0.2}]);
assert.equal(c.messages.length, 1);
assert.ok(c.messages[0].includes('history hint + new mult'));
for (const phrase of ['work it now', "won't last", 'can wait', 'usually stays']) {
  assert.ok(!source.includes(phrase), phrase + ' remains in browser presentation');
}
console.log('Browser urgency: authoritative flag, sorting, alerts and cautious wording passed');
