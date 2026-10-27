/** Ranking metrics from the evaluation spec. Grades: 0 irrelevant, 1 weak, 2 reasonable, 3 best. */

const gain = (grade: number): number => 2 ** grade - 1;

const dcg = (grades: number[], k: number): number =>
  grades.slice(0, k).reduce((sum, grade, index) => sum + gain(grade) / Math.log2(index + 2), 0);

/** nDCG@k over one query's eligible pool; `grades` is in the system's ranked order. */
export function ndcgAt(grades: number[], k = 3): number {
  const ideal = dcg([...grades].sort((a, b) => b - a), k);
  return ideal === 0 ? 0 : dcg(grades, k) / ideal;
}

/** Share of the top k with grade >= threshold, divided by k (not by the number returned). */
export function precisionAt(grades: number[], k = 3, threshold = 2): number {
  return grades.slice(0, k).filter((grade) => grade >= threshold).length / k;
}

/** Share of unequal-grade pairs where the higher grade is ranked first. NaN when no such pair exists. */
export function pairwiseAccuracy(grades: number[]): number {
  let total = 0;
  let correct = 0;
  for (let i = 0; i < grades.length; i++) {
    for (let j = i + 1; j < grades.length; j++) {
      if (grades[i] === grades[j]) continue;
      total += 1;
      if (grades[i] > grades[j]) correct += 1;
    }
  }
  return total === 0 ? Number.NaN : correct / total;
}

/** Mean over values, skipping NaN (for example, a query with no unequal-grade pair). */
export function macroAverage(values: number[]): number {
  const present = values.filter((value) => !Number.isNaN(value));
  return present.length === 0 ? Number.NaN : present.reduce((sum, value) => sum + value, 0) / present.length;
}
