import { spawn, spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { createServer } from 'node:net';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const backend = join(root, 'backend');
const frontend = join(root, 'frontend');
const python = join(backend, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const vite = join(frontend, 'node_modules/vite/bin/vite.js');

export async function assertPortAvailable(port) {
  await new Promise((resolvePort, reject) => {
    const server = createServer();
    server.once('error', () => reject(new Error('Port ' + port + ' is unavailable. Stop the server using it and try again.')));
    server.listen(port, '127.0.0.1', () => server.close(resolvePort));
  });
}

async function stopChild(record) {
  if (!record.child.pid || record.exited) return;
  if (process.platform === 'win32') {
    await new Promise(resolveStop => {
      const killer = spawn('taskkill', ['/pid', String(record.child.pid), '/T', '/F'], { stdio: 'ignore', windowsHide: true });
      killer.once('error', resolveStop);
      killer.once('exit', resolveStop);
    });
  } else {
    try { process.kill(-record.child.pid, 'SIGTERM'); } catch (error) {
      if (error.code !== 'ESRCH') throw error;
    }
  }
  const timeout = setTimeout(() => {
    try {
      if (process.platform === 'win32') record.child.kill('SIGKILL');
      else process.kill(-record.child.pid, 'SIGKILL');
    } catch (error) {
      if (error.code !== 'ESRCH') console.error('Could not stop ' + record.label + '.');
    }
  }, 4000);
  try { await record.finished; } finally { clearTimeout(timeout); }
}

export async function runProcesses(commands, { signal, continuous = false } = {}) {
  if (signal?.aborted) return 0;
  const records = commands.map(({ label, command, args, cwd }) => {
    const child = spawn(command, args, { cwd, stdio: 'inherit', windowsHide: true, detached: process.platform !== 'win32' });
    const record = { child, label, exited: false };
    record.finished = new Promise(resolveExit => {
      child.once('error', () => {
        record.exited = true;
        console.error('Could not launch ' + label + '.');
        resolveExit({ label, code: 1 });
      });
      child.once('exit', (code, exitSignal) => {
        record.exited = true;
        resolveExit({ label, code: code ?? (exitSignal ? 1 : 0) });
      });
    });
    return record;
  });
  let onAbort;
  const aborted = new Promise(resolveAbort => {
    onAbort = () => resolveAbort({ code: 0 });
    signal?.addEventListener('abort', onAbort, { once: true });
    if (signal?.aborted) onAbort();
  });
  try {
    const first = await Promise.race([...records.map(record => record.finished), aborted]);
    if (signal?.aborted) return 0;
    if (continuous) console.error(first.label + ' stopped. Stopping the other development server.');
    return continuous ? first.code || 1 : first.code;
  } finally {
    signal?.removeEventListener('abort', onAbort);
    await Promise.all(records.map(stopChild));
  }
}

async function run(label, command, args, cwd, signal) {
  const code = await runProcesses([{ label, command, args, cwd }], { signal });
  if (signal.aborted) throw new Error('Startup cancelled.');
  if (code !== 0) throw new Error(label + ' failed (exit ' + code + '). Fix the error above and rerun the command.');
}

function probe(command, args) {
  return spawnSync(command, args, { stdio: 'ignore', windowsHide: true }).status === 0;
}

async function prepareBackend(refresh, signal) {
  if (!existsSync(python)) {
    const candidates = process.platform === 'win32' ? [['py', '-3.12'], ['python3.12'], ['python']] : [['python3.12'], ['python3'], ['python']];
    const versionCheck = ['-c', 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)'];
    const selected = candidates.find(([command, ...args]) => probe(command, [...args, ...versionCheck]));
    if (!selected) throw new Error('Python 3.12 was not found. Install it and make it available on PATH.');
    console.log('Creating backend/.venv...');
    await run('Python environment setup', selected[0], [...selected.slice(1), '-m', 'venv', '.venv'], backend, signal);
  }
  const imports = 'import fastapi, google.genai, pydantic, uvicorn, httpx, httpx2, sqlalchemy, dotenv';
  if (refresh || !probe(python, ['-c', imports])) {
    console.log('Installing backend dependencies...');
    await run('Backend dependency installation', python, ['-m', 'pip', 'install', '-r', 'requirements.txt'], backend, signal);
  }
  await run('Backend configuration', python, [join(root, 'scripts/setup_env.py')], backend, signal);
}

async function prepareFrontend(refresh, signal) {
  if (refresh || !existsSync(vite) || !existsSync(join(frontend, 'node_modules/.package-lock.json'))) {
    const npm = process.env.npm_execpath;
    if (!npm || !existsSync(npm)) throw new Error('Run this launcher through npm run so it can locate npm.');
    console.log('Installing frontend dependencies...');
    await run('Frontend dependency installation', process.execPath, [npm, 'ci'], frontend, signal);
  }
}

export async function main(args = process.argv.slice(2)) {
  if (args.includes('--help')) {
    console.log('From the repository root:\n  npm run start:all\n  npm run start:frontend\n  npm run start:backend\n  npm run start:all:setup\n  npm run setup\n\nUse -- --setup with any start command to refresh its dependencies.\nMissing dependencies and backend settings are handled on first start.');
    return 0;
  }
  const [target, ...flags] = args;
  if (!['all', 'frontend', 'backend', 'setup'].includes(target) || flags.some(flag => flag !== '--setup')) {
    console.error('Use start:all, start:frontend, start:backend, start:all:setup, or setup. Optional flag: --setup.');
    return 1;
  }
  const controller = new AbortController();
  const interrupt = () => controller.abort();
  process.once('SIGINT', interrupt);
  process.once('SIGTERM', interrupt);
  try {
    const needsBackend = target !== 'frontend';
    const needsFrontend = target !== 'backend';
    const refresh = target === 'setup' || flags.includes('--setup');
    if (target !== 'setup') {
      if (needsBackend) await assertPortAvailable(8000);
      if (needsFrontend) await assertPortAvailable(5173);
    }
    if (needsBackend) await prepareBackend(refresh, controller.signal);
    if (needsFrontend) await prepareFrontend(refresh, controller.signal);
    if (target === 'setup') {
      console.log('Setup complete. Run npm run start:all to start FableAI.');
      return 0;
    }
    const commands = [];
    if (needsBackend) {
      commands.push({ label: 'Backend', command: python, args: ['-m', 'uvicorn', 'app.main:app', '--reload', '--host', '127.0.0.1', '--port', '8000'], cwd: backend });
      console.log('Backend: http://127.0.0.1:8000');
    }
    if (needsFrontend) {
      commands.push({ label: 'Frontend', command: process.execPath, args: [vite], cwd: frontend });
      console.log('Frontend: http://127.0.0.1:5173');
    }
    console.log('Press Ctrl+C to stop.');
    return await runProcesses(commands, { signal: controller.signal, continuous: true });
  } catch (error) {
    if (!controller.signal.aborted) console.error(error.message);
    return controller.signal.aborted ? 0 : 1;
  } finally {
    process.removeListener('SIGINT', interrupt);
    process.removeListener('SIGTERM', interrupt);
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = await main();
}
