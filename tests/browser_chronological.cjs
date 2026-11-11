// Production browser sorting must use chronological evidence across UTC midnight.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[2] || 'pouncer/web.py', 'utf8');
const time = source.slice(source.indexOf('  function timeVal(s)'), source.indexOf('  // Classification is supplied'));
const sorting = source.slice(source.indexOf('  function sorted(spots'), source.indexOf('  // Calm list:'));
const c = vm.createContext({sortKey:'time', freqFrom:false, rareFirst:false, dxFirst:false, multFirst:false});
vm.runInContext(time + sorting, c);
const order = spots => Array.from(c.sorted(spots, 14025), s => s.call);
assert.deepEqual(order([
  {call:'OLD', time:'2359Z', received:1000000000, age:180},
  {call:'NEW', time:'0001Z', received:1000000120, age:60},
  {call:'UNKNOWN', time:'2358Z'}
]), ['NEW','OLD','UNKNOWN']);
// Age is available from older server versions; do not reinterpret its clock label.
assert.deepEqual(order([
  {call:'OLD', time:'2359Z', age:180}, {call:'NEW', time:'0001Z', age:60}
]), ['NEW','OLD']);
assert.deepEqual(order([
  {call:'A', time:'2359Z', received:1000000000},
  {call:'B', time:'0001Z', received:1000086520},
  {call:'BAD', received:NaN}
]), ['B','A','BAD']);
console.log('Chronological browser sorting: midnight, multi-day, legacy age and missing time passed');
