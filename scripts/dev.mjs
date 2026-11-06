import { spawn, spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { createServer } from 'node:net';
import { dirname, join, resolve } from 'node:path';
import { loadEnvFile } from 'node:process';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const backend = join(root, 'api');
const frontend = join(root, 'frontend');
const python = join(backend, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const vite = join(frontend, 'node_modules/vite/bin/vite.js');
const composeArgs = ['compose', '-f', 'docker-compose.yml'];
const developmentComposeArgs = [...composeArgs, '-f', 'docker-compose.dev.yml'];

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
    if (continuous) console.error(first.label + ' stopped (exit ' + first.code + '). Stopping the other development server.');
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
    console.log('Creating api/.venv...');
    await run('Python environment setup', selected[0], [...selected.slice(1), '-m', 'venv', '.venv'], backend, signal);
  }
  const imports = 'import fastapi, google.genai, pydantic, uvicorn, httpx, httpx2, sqlalchemy, dotenv, alembic, psycopg';
  if (refresh || !probe(python, ['-c', imports])) {
    console.log('Installing backend dependencies...');
    await run('Backend dependency installation', python, ['-m', 'pip', 'install', '-r', 'requirements.txt'], backend, signal);
  }
  await run('Backend configuration', python, [join(root, 'scripts/setup_env.py')], backend, signal);
}

export function developmentDatabaseUrl({ user, password, database }) {
  return 'postgresql://' + encodeURIComponent(user) + ':' + encodeURIComponent(password) + '@127.0.0.1:5432/' + encodeURIComponent(database);
}

function runningComposeServices() {
  const result = spawnSync('docker', [...composeArgs, 'ps', '--status', 'running', '--services'], { cwd: root, encoding: 'utf8', windowsHide: true });
  if (result.error || result.status !== 0) throw new Error('Could not inspect Docker Compose services. Ensure Docker is running.');
  return result.stdout.split(/\r?\n/).map(service => service.trim()).filter(Boolean);
}

async function prepareDevelopmentPostgres(signal) {
  const envFile = join(root, '.env');
  if (existsSync(envFile)) loadEnvFile(envFile);
  const password = process.env.POSTGRES_PASSWORD;
  if (!password) throw new Error('Set POSTGRES_PASSWORD in the ignored root .env file or environment before starting the local backend.');

  const apiWasRunning = runningComposeServices().includes('api');
  if (apiWasRunning) {
    console.log('Stopping the Docker Compose API to free port 8000 for local development.');
    await run('Docker Compose API shutdown', 'docker', [...developmentComposeArgs, 'stop', 'api'], root, signal);
  }

  try {
    await run('Docker PostgreSQL startup', 'docker', [...developmentComposeArgs, 'up', '-d', '--wait', '--wait-timeout', '60', 'postgres'], root, signal);
    process.env.FABLEAI_DATABASE_URL = developmentDatabaseUrl({
      user: process.env.POSTGRES_USER || 'fableai',
      password,
      database: process.env.POSTGRES_DB || 'fableai',
    });
    return apiWasRunning;
  } catch (error) {
    if (apiWasRunning) {
      await run('Docker Compose API restoration', 'docker', [...developmentComposeArgs, 'up', '-d', '--wait', '--wait-timeout', '60', 'api'], root, new AbortController().signal);
    }
    throw error;
  }
}

async function prepareFrontend(refresh, signal) {
  if (refresh || !existsSync(vite) || !existsSync(join(frontend, 'node_modules/.package-lock.json'))) {
    const npm = process.env.npm_execpath;
    if (!npm || !existsSync(npm)) throw new Error('Run this launcher through npm run so it can locate npm.');
    console.log('Installing frontend dependencies...');
    await run('Frontend dependency installation', process.execPath, [npm, 'ci'], frontend, signal);
  }
}

export function backendCommand(app = 'app.main:app', cwd = backend, port = 8000) {
  const args = process.platform === 'win32'
    ? [join(root, 'scripts/dev_backend.py'), app, String(port)]
    : ['-m', 'uvicorn', app, '--reload', '--host', '127.0.0.1', '--port', String(port)];
  return { label: 'Backend', command: python, args, cwd };
}

export async function main(args = process.argv.slice(2)) {
  if (args.includes('--help')) {
    console.log('From the repository root:\n  npm run start:all\n  npm run start:frontend\n  npm run start:backend\n  npm run start:all:setup\n  npm run setup\n  npm run db:migrate\n\nUse -- --setup with any start command to refresh its dependencies.\nMissing dependencies and backend settings are handled on first start.');
    return 0;
  }
  const [target, ...flags] = args;
  if (!['all', 'frontend', 'backend', 'setup', 'migrate'].includes(target) || flags.some(flag => flag !== '--setup')) {
    console.error('Use start:all, start:frontend, start:backend, start:all:setup, setup, or db:migrate. Optional flag: --setup.');
    return 1;
  }
  const controller = new AbortController();
  const interrupt = () => controller.abort();
  process.once('SIGINT', interrupt);
  process.once('SIGTERM', interrupt);
  let dockerApiWasRunning = false;
  let exitCode = 0;
  try {
    if (target === 'migrate') {
      if (!existsSync(python)) throw new Error('Set up api/.venv before running database migrations.');
      return await runProcesses([{ label: 'Database migrations', command: python, args: ['-m', 'alembic', '-c', 'alembic.ini', 'upgrade', 'head'], cwd: backend }], { signal: controller.signal });
    }
    const needsBackend = target !== 'frontend';
    const needsFrontend = target !== 'backend';
    const refresh = target === 'setup' || flags.includes('--setup');
    if (target !== 'setup') {
      if (needsFrontend) await assertPortAvailable(5173);
    }
    if (needsBackend && target !== 'setup') {
      dockerApiWasRunning = await prepareDevelopmentPostgres(controller.signal);
      await assertPortAvailable(8000);
    }
    if (needsBackend) await prepareBackend(refresh, controller.signal);
    if (needsBackend && target !== 'setup') {
      await run('Database migrations', python, ['-m', 'alembic', '-c', 'alembic.ini', 'upgrade', 'head'], backend, controller.signal);
    }
    if (needsFrontend) await prepareFrontend(refresh, controller.signal);
    if (target === 'setup') {
      console.log('Setup complete. Run npm run start:all to start FableAI.');
      exitCode = 0;
    } else {
      const commands = [];
      if (needsBackend) {
        commands.push(backendCommand());
        console.log('Backend: http://127.0.0.1:8000');
      }
      if (needsFrontend) {
        commands.push({ label: 'Frontend', command: process.execPath, args: [vite], cwd: frontend });
        console.log('Frontend: http://127.0.0.1:5173');
      }
      console.log('Press Ctrl+C to stop.');
      exitCode = await runProcesses(commands, { signal: controller.signal, continuous: true });
    }
  } catch (error) {
    if (!controller.signal.aborted) console.error(error.message);
    exitCode = controller.signal.aborted ? 0 : 1;
  } finally {
    if (dockerApiWasRunning) {
      try {
        await run('Docker Compose API restoration', 'docker', [...developmentComposeArgs, 'up', '-d', '--wait', '--wait-timeout', '60', 'api'], root, new AbortController().signal);
      } catch {
        console.error('Could not restore the Docker Compose API after local development stopped.');
        exitCode = 1;
      }
    }
    process.removeListener('SIGINT', interrupt);
    process.removeListener('SIGTERM', interrupt);
  }
  return exitCode;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = await main();
}
