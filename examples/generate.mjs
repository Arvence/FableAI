import { randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';

export async function generate({ baseUrl = 'http://127.0.0.1:8000', apiKey, projectId, instruction }) {
  const url = new URL(baseUrl);
  if (!/^https?:$/.test(url.protocol) || url.username || url.password || url.search || url.hash || url.pathname !== '/') throw new Error('Supply a valid API origin.');
  if (url.protocol !== 'https:' && !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)) throw new Error('Use HTTPS outside loopback.');
  if (typeof apiKey !== 'string' || !/^fableai_[0-9a-f]{32}_[A-Za-z0-9_-]{43}$/.test(apiKey) || !/^[a-z0-9][a-z0-9_-]{0,99}$/.test(projectId ?? '') || !instruction?.trim() || instruction.length > 4000) throw new Error('Set FABLEAI_API_KEY and supply a project ID and an instruction of 1 to 4000 characters.');
  const requestId = randomUUID();
  const response = await fetch(new URL('/api/v1/projects/' + projectId + '/generations', url), {
    method: 'POST',
    headers: { Authorization: 'Bearer ' + apiKey, 'Content-Type': 'application/json', 'Idempotency-Key': requestId },
    body: JSON.stringify({ instruction }),
    redirect: 'error',
    signal: AbortSignal.timeout(75_000),
  });
  if (!response.ok) throw new Error('Generation failed (HTTP ' + response.status + '). No retry was made.');
  if (response.headers.get('Idempotency-Key') !== requestId) throw new Error('The response did not match this request. No retry was made.');
  const result = await response.json();
  if (!Array.isArray(result.entries) || !result.entries.length) throw new Error('The API returned no drafts.');
  return result;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const result = await generate({ baseUrl: process.env.FABLEAI_API_URL, apiKey: process.env.FABLEAI_API_KEY, projectId: process.argv[2], instruction: process.argv.slice(3).join(' ') });
    console.log(JSON.stringify(result, null, 2));
  } catch {
    console.error('Generation could not complete. Check the API URL, project credential, project ID, and instruction. No retry was made.');
    process.exitCode = 1;
  }
}
