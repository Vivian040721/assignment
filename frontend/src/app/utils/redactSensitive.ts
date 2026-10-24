const sensitiveKey = /api[_-]?key|authorization|access[_-]?token|secret|password|bearer/i;

export function redactSensitiveData(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(redactSensitiveData);
  if (typeof value !== 'object' || value === null) return value;
  return Object.fromEntries(Object.entries(value).map(([key, nested]) => [
    key,
    sensitiveKey.test(key) ? '[已隐藏]' : redactSensitiveData(nested),
  ]));
}
