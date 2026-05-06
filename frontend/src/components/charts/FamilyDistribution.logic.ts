export interface FamilyBar {
  name: string;
  value: number;
  isOther?: boolean;
}

export function aggregateLongTail(
  data: Record<string, number>,
  topN = 10,
): FamilyBar[] {
  const sorted: FamilyBar[] = Object.entries(data)
    .sort(([, a], [, b]) => b - a)
    .map(([name, value]) => ({ name, value }));
  if (sorted.length <= topN) return sorted;
  const top = sorted.slice(0, topN);
  const tail = sorted.slice(topN);
  const tailSum = tail.reduce((acc, bar) => acc + bar.value, 0);
  return [
    ...top,
    { name: `Other (${tail.length})`, value: tailSum, isOther: true },
  ];
}
