import { defineConfig } from '@playwright/test';
import { fileURLToPath } from 'node:url';

const backend = fileURLToPath(new URL('../backend', import.meta.url));
const python = fileURLToPath(new URL(process.platform === 'win32' ? '../backend/.venv/Scripts/python.exe' : '../backend/.venv/bin/python', import.meta.url));

export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:5173',
    channel: process.env.PLAYWRIGHT_CHANNEL,
    viewport: { width: 1365, height: 950 },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  webServer: [
    { command: `"${python}" tests/e2e_server.py`, cwd: backend, url: 'http://127.0.0.1:8000/health', reuseExistingServer: false, timeout: 30000 },
    { command: `${process.platform === 'win32' ? 'npm.cmd' : 'npm'} run dev`, url: 'http://127.0.0.1:5173', reuseExistingServer: false, timeout: 30000 },
  ],
});
