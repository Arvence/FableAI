export function readSession<T>(key: string, fallback: T): T {
  try {
    const value = sessionStorage.getItem(`fableai:${key}`);
    return value ? JSON.parse(value) as T : fallback;
  } catch {
    return fallback;
  }
}

export function writeSession(key: string, value: unknown): boolean {
  try {
    if (value === null) sessionStorage.removeItem(`fableai:${key}`);
    else sessionStorage.setItem(`fableai:${key}`, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}
