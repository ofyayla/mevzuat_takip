'use strict';

const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

for (const view of ['gelen', 'arsiv', 'yonetim']) {
  test(`${view} renders the app bar and main content without the removed footer`, () => {
    const app = {};
    const context = vm.createContext({
      document: {
        documentElement: {},
        querySelector: selector => selector === '#app' ? app : null,
        addEventListener() {},
      },
      addEventListener() {},
      setInterval() {},
    });
    for (const file of ['api.js', 'inbox.js', 'views.js']) {
      vm.runInContext(readFileSync(path.join(__dirname, '..', 'assets', file), 'utf8'), context, { filename: file });
    }
    vm.runInContext('loadRefs = () => new Promise(() => {});', context);
    vm.runInContext(readFileSync(path.join(__dirname, '..', 'assets', 'app.js'), 'utf8'), context, { filename: 'app.js' });
    vm.runInContext(`
      S.view = '${view}';
      appbar = () => '<header>Portal</header>';
      VIEWS[S.view] = () => '<section>${view}</section>';
      renderAll();
    `, context);

    assert.doesNotMatch(app.innerHTML, /<footer\b|Tarihler gg\.aa\.yyyy/);
    assert.equal(app.innerHTML, `<header>Portal</header><main id="main"><section>${view}</section></main>`);
  });
}
