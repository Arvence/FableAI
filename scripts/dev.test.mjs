import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync, utimesSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import test from 'node:test';
import { assertPortAvailable, backendCommand, developmentDatabaseUrl, main, runProcesses } from './dev.mjs';

async function waitFor(predicate) {
  const deadline = Date.now() + 10000;
  while (!(await predicate())) {
    if (Date.now() > deadline) throw new Error('Timed out waiting for a test process.');
    await delay(50);
  }
}

function alive(pid) {
  try { process.kill(pid, 0); return true; } catch { return false; }
}

function service(marker) {
  const grandchild = 'setInterval(() => {}, 1000)';
  const script = [
    "const { spawn } = require('node:child_process');",
    "const { writeFileSync } = require('node:fs');",
    'const child = spawn(process.execPath, ["-e", ' + JSON.stringify(grandchild) + '], { stdio: "ignore", windowsHide: true });',
    'writeFileSync(' + JSON.stringify(marker) + ', JSON.stringify([process.pid, child.pid]));',
    'setInterval(() => {}, 1000);',
  ].join('\n');
  return { label: 'Test service', command: process.execPath, args: ['-e', script] };
}

function cleanup(pids) {
  for (const pid of pids) {
    if (!alive(pid)) continue;
    if (process.platform === 'win32') {
      spawnSync('taskkill', ['/pid', String(pid), '/T', '/F'], { stdio: 'ignore', windowsHide: true });
    } else {
      try { process.kill(pid, 'SIGKILL'); } catch { /* The process may have just exited. */ }
    }
  }
}

test('a foreground command reports its exit code', async () => {
  const command = { label: 'Test command', command: process.execPath, args: ['-e', 'process.exit(0)'] };
  assert.equal(await runProcesses([command]), 0);
});

test('cancelling startup stops the server and its descendants', async () => {
  const directory = mkdtempSync(join(tmpdir(), 'fableai-launcher-'));
  const marker = join(directory, 'processes.json');
  const controller = new AbortController();
  let pids = [];
  const running = runProcesses([service(marker)], { signal: controller.signal, continuous: true });
  try {
    await waitFor(() => existsSync(marker));
    pids = JSON.parse(readFileSync(marker, 'utf8'));
    assert.ok(pids.every(alive));
    controller.abort();
    assert.equal(await running, 0);
    await waitFor(() => pids.every(pid => !alive(pid)));
  } finally {
    controller.abort();
    await running;
    cleanup(pids);
    rmSync(directory, { recursive: true, force: true });
  }
});

test('a failed service stops its peer and descendants and returns the failure', async () => {
  const directory = mkdtempSync(join(tmpdir(), 'fableai-launcher-'));
  const marker = join(directory, 'processes.json');
  const controller = new AbortController();
  const failure = "const fs = require('node:fs'); const timer = setInterval(() => { if (fs.existsSync(" + JSON.stringify(marker) + ")) { clearInterval(timer); process.exit(7); } }, 50);";
  let pids = [];
  const running = runProcesses([service(marker), { label: 'Failing service', command: process.execPath, args: ['-e', failure] }], { signal: controller.signal, continuous: true });
  try {
    assert.equal(await running, 7);
    pids = JSON.parse(readFileSync(marker, 'utf8'));
    await waitFor(() => pids.every(pid => !alive(pid)));
  } finally {
    controller.abort();
    await running;
    cleanup(pids);
    rmSync(directory, { recursive: true, force: true });
  }
});

test('an unexpected clean service exit is still a startup failure', async () => {
  assert.equal(await runProcesses([{ label: 'Stopped service', command: process.execPath, args: ['-e', 'process.exit(0)'] }], { continuous: true }), 1);
});

test('an executable that cannot launch returns a failure', async () => {
  assert.equal(await runProcesses([{ label: 'Missing executable', command: join(tmpdir(), 'fableai-nonexistent-executable'), args: [] }]), 1);
});

test('an occupied port is reported without disturbing its listener', async () => {
  const server = createServer();
  await new Promise(resolveListen => server.listen(0, '127.0.0.1', resolveListen));
  const port = server.address().port;
  try {
    await assert.rejects(assertPortAvailable(port), /is unavailable/);
    assert.ok(server.listening);
  } finally {
    await new Promise(resolveClose => server.close(resolveClose));
  }
  await assertPortAvailable(port);
});

test('the local PostgreSQL URL safely encodes credentials and database names', () => {
  const url = new URL(developmentDatabaseUrl({ user: 'local:user', password: 'p@ss:/word%', database: 'fable ai' }));
  assert.equal(url.protocol, 'postgresql:');
  assert.equal(url.hostname, '127.0.0.1');
  assert.equal(url.port, '5432');
  assert.equal(decodeURIComponent(url.username), 'local:user');
  assert.equal(decodeURIComponent(url.password), 'p@ss:/word%');
  assert.equal(decodeURIComponent(url.pathname.slice(1)), 'fable ai');
});

test('invalid flags fail before setup or server startup', async () => {
  assert.equal(await main(['all', '--unknown']), 1);
});

test('an already cancelled operation never starts a process', async () => {
  const controller = new AbortController();
  controller.abort();
  assert.equal(await runProcesses([{ label: 'Never launched', command: 'missing', args: [] }], { signal: controller.signal }), 0);
});


test('Windows backend reload keeps the launcher and peer alive, then cancellation releases the port', { skip: process.platform !== 'win32', timeout: 20000 }, async () => {
  const directory = mkdtempSync(join(tmpdir(), 'fableai-reload-'));
  const marker = join(directory, 'peer.json');
  const application = join(directory, 'fixture.py');
  writeFileSync(application, [
    'import os',
    'from fastapi import FastAPI',
    'app = FastAPI()',
    '@app.get("/")',
    'def health():',
    '    return os.getpid()',
  ].join('\n'));
  const server = createServer();
  await new Promise(resolveListen => server.listen(0, '127.0.0.1', resolveListen));
  const port = server.address().port;
  await new Promise(resolveClose => server.close(resolveClose));
  const controller = new AbortController();
  let settled = false;
  let pids = [];
  let originalWorker;
  let currentWorker;
  const backend = backendCommand('fixture:app', directory, port);
  const running = runProcesses([backend, service(marker)], { signal: controller.signal, continuous: true }).then(code => { settled = true; return code; });
  const worker = async () => {
    try {
      const response = await fetch('http://127.0.0.1:' + port, { headers: { Connection: 'close' }, signal: AbortSignal.timeout(1000) });
      return response.ok ? Number(await response.text()) : null;
    } catch { return null; }
  };
  try {
    await waitFor(async () => existsSync(marker) && (originalWorker = await worker()));
    pids = JSON.parse(readFileSync(marker, 'utf8'));
    await delay(500);
    const changed = new Date(Date.now());
    utimesSync(application, changed, changed);
    await waitFor(async () => (currentWorker = await worker()) && currentWorker !== originalWorker);
    assert.equal(settled, false, 'Reload must not end the launcher.');
    assert.ok(pids.every(alive), 'The peer and its descendants must survive reload.');
    assert.equal(await worker(), currentWorker);
    controller.abort();
    assert.equal(await running, 0);
    await waitFor(() => [...pids, originalWorker, currentWorker].every(pid => !alive(pid)));
    await assertPortAvailable(port);
  } finally {
    controller.abort();
    await running;
    cleanup([...pids, originalWorker, currentWorker].filter(Boolean));
    rmSync(directory, { recursive: true, force: true });
  }
});


test('a Windows backend import failure exits instead of leaving only its watcher alive', { skip: process.platform !== 'win32', timeout: 10000 }, async () => {
  const directory = mkdtempSync(join(tmpdir(), 'fableai-startup-failure-'));
  const marker = join(directory, 'peer.json');
  const server = createServer();
  await new Promise(resolveListen => server.listen(0, '127.0.0.1', resolveListen));
  const port = server.address().port;
  await new Promise(resolveClose => server.close(resolveClose));
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5000);
  let pids = [];
  const running = runProcesses([backendCommand('missing_fableai_fixture:app', directory, port), service(marker)], { signal: controller.signal, continuous: true });
  try {
    assert.equal(await running, 3);
    pids = JSON.parse(readFileSync(marker, 'utf8'));
    await waitFor(() => pids.every(pid => !alive(pid)));
    await assertPortAvailable(port);
  } finally {
    clearTimeout(timeout);
    controller.abort();
    await running;
    cleanup(pids);
    rmSync(directory, { recursive: true, force: true });
  }
});
