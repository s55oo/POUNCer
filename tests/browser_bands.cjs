// Run with: node tests/browser_bands.cjs
// Exercise the actual browser menu functions with a small DOM, without a radio.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
// Windows checkouts have CRLF line endings: the slices below look for '\n'
const source = fs.readFileSync('pouncer/web.py', 'utf8').replace(/\r\n/g, '\n');
const code = source.slice(source.indexOf('  let selectedBands ='), source.indexOf('  bandEl.onclick = openBands;'));
function element() {
  return {
    children: [], dataset: {}, hidden: false, checked: false, disabled: false,
    classList: {values: new Set(), add(v) { this.values.add(v); }, remove(v) { this.values.delete(v); },
      toggle(v, on) { if (on) this.add(v); else this.remove(v); }},
    appendChild(child) { this.children.push(child); },
    setAttribute(k, v) { this[k] = v; },
    set textContent(v) { this.text = v; this.children = []; },
    get textContent() { return this.text; },
    querySelectorAll() { return this.children.filter(b => b.dataset.band); },
  };
}
const elements = Object.fromEntries(['multibands', 'applybands', 'bandfor'].map(id => [id, element()]));
const context = vm.createContext({
  document: {getElementById: id => elements[id], createElement: element},
  bandGrid: element(), bandSheet: element(), views: {}, selKey: '',
  lastData: {radio: 1, band: '20m', radio_band: '20m', rbands: {'1': '20m', '2': '40m'}, radios: [1,2]},
  saveViews() { context.saved = JSON.stringify(context.views); },
  refresh() { context.refreshed = (context.refreshed || 0) + 1; }, toast() {},
});
vm.runInContext(code, context);
function band(b) { return context.bandGrid.children.find(el => el.dataset.band === b); }
function select(...bands) {
  elements.multibands.checked = true;
  elements.multibands.onchange();
  for (const button of context.bandGrid.querySelectorAll()) {
    if ((button['aria-pressed'] === 'true') !== bands.includes(button.dataset.band)) button.onclick();
  }
}
context.openBands();
assert.equal(elements.multibands.checked, false);
band('80m').onclick();
assert.equal(context.views['1'].band, '80m'); // old immediate single-band behavior
context.openBands();
select('80m', '40m', '15m');
assert.equal(band('40m')['aria-pressed'], 'true');
assert.equal(elements.applybands.hidden, false);
elements.applybands.onclick();
assert.equal(context.views['1'].band, '80m,40m,15m');
assert.equal(context.views['1'].from, '20m');
context.views = JSON.parse(context.saved); // browser persistence uses the existing format
context.lastData.radio_band = '40m';
context.openBands();
assert.equal(elements.multibands.checked, true);
assert.equal(band('80m')['aria-pressed'], 'true');
assert.equal(band('40m')['aria-pressed'], 'true');
// The rendering path must retain multiple bands after QSY, but release a single band.
const follow = source.slice(source.indexOf('    {\n      const v = views[String(cur)];'), source.indexOf('    if (data.view) meta'));
context.cur = 1; context.data = {radio_band: '40m'};
vm.runInContext(follow, context);
assert.equal(context.views['1'].band, '80m,40m,15m');
context.lastData.radio = 2;
context.openBands();
select('80m', '40m');
elements.applybands.onclick();
assert.equal(context.views['2'].band, '80m,40m');
assert.equal(context.views['1'].band, '80m,40m,15m');
context.openBands(); select();
assert.equal(elements.applybands.disabled, true);
context.chooseBand('ALL');
assert.equal(context.views['2'].band, 'ALL');
context.chooseBand('40m');
assert.equal(context.views['2'], undefined); // follow current radio
context.views['1'] = {band: '80m', from: '20m'};
vm.runInContext(follow, context);
assert.equal(context.views['1'], undefined); // previous single-band preferences still follow
console.log('Browser band menu: single, multiple, ALL, follow, persistence and per-radio isolation passed');
