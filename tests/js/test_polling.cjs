const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const createPolling = require('../../app/static/js/polling.js');
const flush = () => new Promise(resolve => setImmediate(resolve));
const config = {revision: 7, view: 'style1', intervalMs: 5000, updatesUrl: '/check-updates', stateUrl: '/state'};

function harness(t) {
    let nextTimer = 0, reloads = 0;
    const timers = new Map(), calls = [], warnings = [];
    const env = {
        AbortController,
        setTimeout(callback, delay) { const id = ++nextTimer; timers.set(id, {callback, delay}); return id; },
        clearTimeout(id) { timers.delete(id); },
        fetch(url, options) {
            return new Promise((resolve, reject) => {
                calls.push({url, signal: options.signal, resolve, reject});
                options.signal.addEventListener('abort', () => reject(new Error('aborted')), {once: true});
            });
        },
        document: {getElementById() { return null; }, querySelectorAll() { return []; }},
        console: {warn(...args) { warnings.push(args); }},
        location: {reload() { reloads += 1; }},
    };
    const controller = createPolling(config, env);
    t.after(() => controller.stop());
    function reply(index, value, ok = true) {
        calls[index].resolve({ok, status: ok ? 200 : 500, json: async () => value});
    }
    function fire(delay) {
        const matches = [...timers].filter(([, timer]) => timer.delay === delay);
        assert.ok(matches.length, 'expected timer at ' + delay);
        for (const [id, timer] of matches) { timers.delete(id); timer.callback(); }
    }
    return {controller, env, timers, calls, warnings, reply, fire, reloads: () => reloads};
}

test('unchanged data/view keep polling without reload and pass the revision', async t => {
    const h = harness(t); h.controller.start();
    assert.equal(h.calls[0].url, '/check-updates?revision=7&category=current_race');
    h.reply(0, {revision: 7, updated: false}); h.reply(1, {view: 'style1'});
    await flush(); assert.equal(h.reloads(), 0); assert.equal(h.warnings.length, 0);
    h.fire(5000); assert.equal(h.calls.length, 4);
    assert.match(h.calls[2].url, /revision=7&category=current_race/);
});

test('repeated start does not create duplicate requests', t => {
    const h = harness(t); h.controller.start(); h.controller.start();
    assert.equal(h.calls.length, 2);
});

test('simultaneous data and view changes schedule only one reload', async t => {
    const h = harness(t); h.controller.start();
    h.reply(0, {revision: 8, updated: true}); h.reply(1, {view: 'style2'});
    await flush(); assert.equal(h.timers.size, 1);
    h.fire(800); assert.equal(h.reloads(), 1); assert.equal(h.timers.size, 0);
    assert.equal(h.warnings.length, 0);
});

test('view change alone reloads and missing indicator is safe', async t => {
    const h = harness(t); h.controller.start(); h.reply(1, {view: 'style2'});
    await flush(); h.fire(800);
    assert.equal(h.reloads(), 1); assert.equal(h.calls[0].signal.aborted, true);
});

test('unchanged view is compared with style1, not race template name', async t => {
    const h = harness(t); h.controller.start(); h.reply(1, {view: 'style1'});
    await flush(); assert.equal(h.reloads(), 0);
    assert.equal([...h.timers.values()].some(timer => timer.delay === 800), false);
});

test('network errors retry with backoff without reload', async t => {
    const h = harness(t); h.controller.start();
    h.calls[0].reject(new Error('offline')); h.reply(1, {view: 'style1'});
    await flush(); assert.equal(h.warnings.length, 1);
    h.fire(10000); assert.equal(h.calls.length, 3); assert.equal(h.reloads(), 0);
});

test('HTTP error and malformed response use retry, not reload', async t => {
    const h = harness(t); h.controller.start();
    h.reply(0, {}, false); h.reply(1, {view: 'unknown'});
    await flush(); assert.equal(h.warnings.length, 2); assert.equal(h.reloads(), 0);
});

test('request timeout aborts and retries', async t => {
    const h = harness(t); h.controller.start(); h.reply(1, {view: 'style1'});
    await flush(); h.fire(35000); await flush();
    assert.equal(h.calls[0].signal.aborted, true); assert.equal(h.reloads(), 0);
    h.fire(10000); assert.equal(h.calls.length, 3);
});

test('stop aborts requests and removes timers', async t => {
    const h = harness(t); h.controller.start(); h.controller.stop(); await flush();
    assert.ok(h.calls.every(call => call.signal.aborted));
    assert.equal(h.timers.size, 0); assert.equal(h.warnings.length, 0);
});

test('stop before scheduled reload cancels it', async t => {
    const h = harness(t); h.controller.start(); h.reply(0, {revision: 8, updated: true});
    await flush(); h.controller.stop();
    assert.equal(h.timers.size, 0); assert.equal(h.reloads(), 0);
});

test('restart ignores completions from the previous generation', async t => {
    const h = harness(t); h.controller.start(); h.controller.stop(); h.controller.start();
    h.reply(2, {revision: 7, updated: false}); h.reply(3, {view: 'style1'});
    await flush(); assert.equal(h.calls.length, 4); assert.equal(h.timers.size, 2);
    assert.equal(h.warnings.length, 0);
});

test('browser bootstrap starts once and registers page lifecycle handlers', async t => {
    const h = harness(t), handlers = {};
    h.env.document.getElementById = id => id === 'karting-poll-config' ? {textContent: JSON.stringify(config)} : null;
    h.env.addEventListener = (name, handler) => { handlers[name] = handler; };
    const script = fs.readFileSync(path.join(__dirname, '../../app/static/js/polling.js'), 'utf8');
    const context = vm.createContext({window: h.env});
    vm.runInContext(script, context); vm.runInContext(script, context);
    assert.equal(h.calls.length, 2);
    handlers.pagehide(); await flush(); assert.equal(h.timers.size, 0);
    handlers.pageshow({persisted: true}); assert.equal(h.calls.length, 4);
    h.env.kartingPolling.stop(); await flush();
});
