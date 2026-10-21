const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const template = readFileSync(path.join(__dirname, 'templates/accounts/components/passkey_setup_prompt.html'), 'utf8');
const script = template.match(/<script>([\s\S]*?)<\/script>/)[1];

function prompt(cookie = '') {
  const timers = new Map();
  let nextId = 0;
  let setupClick;
  const box = {
    dataset: { dismissCookie: 'fc_pk_prompt_42' },
    hidden: true,
    removed: false,
    style: {},
    classList: { remove() { box.hidden = false; } },
    querySelector() {
      return { addEventListener(event, callback) { setupClick = callback; } };
    },
    remove() { box.removed = true; },
  };
  const document = { cookie, getElementById() { return box; } };
  vm.runInNewContext(script, {
    document,
    setTimeout(callback, delay) {
      timers.set(++nextId, { callback, delay });
      return nextId;
    },
    clearTimeout(id) { timers.delete(id); },
  });
  return { box, document, timers, setup() { setupClick(); } };
}

test('prompt fades after five seconds, remembers dismissal, and is removed', () => {
  const view = prompt();
  assert.equal(view.box.hidden, false);
  const hide = view.timers.get(1);
  assert.equal(hide.delay, 5000);
  hide.callback();
  assert.equal(view.box.style.opacity, '0');
  assert.match(view.document.cookie, /fc_pk_prompt_42=1; max-age=2592000;/);
  const remove = view.timers.get(2);
  assert.equal(remove.delay, 500);
  remove.callback();
  assert.equal(view.box.removed, true);
});

test('already dismissed prompt stays hidden without scheduling a timer', () => {
  const view = prompt('other=1; fc_pk_prompt_42=1');
  assert.equal(view.box.hidden, true);
  assert.equal(view.timers.size, 0);
});

test('starting passkey setup cancels dismissal so results stay visible', () => {
  const view = prompt();
  view.setup();
  assert.equal(view.timers.size, 0);
  assert.equal(view.box.removed, false);
  assert.equal(view.document.cookie, '');
});
