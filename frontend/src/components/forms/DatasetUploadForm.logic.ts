export const MAX_CSV_BYTES = 10 * 1024 * 1024;

export function checkCsvSize(csv: string): string | null {
  const bytes = new Blob([csv]).size;
  if (bytes > MAX_CSV_BYTES) {
    return `CSV size ${(bytes / 1024 / 1024).toFixed(2)} MB exceeds limit of 10 MB`;
  }
  return null;
}
