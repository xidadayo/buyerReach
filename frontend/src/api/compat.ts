export type CandidateRating = 'A' | 'B' | 'C' | 'D' | 'pending' | 'unknown'

export function safeRating(value: unknown): CandidateRating {
  return value === 'A' || value === 'B' || value === 'C' || value === 'D' || value === 'pending'
    ? value
    : 'unknown'
}

export function safeCandidateStatus(value: unknown): string {
  return typeof value === 'string' && value.trim() ? value : 'unknown'
}

export type EvaluationStatus =
  | 'pending'
  | 'running'
  | 'insufficient_data'
  | 'completed'
  | 'failed'
  | 'unknown'

export function safeEvaluationStatus(value: unknown): EvaluationStatus {
  return value === 'pending' ||
    value === 'running' ||
    value === 'insufficient_data' ||
    value === 'completed' ||
    value === 'failed'
    ? value
    : 'unknown'
}

export function safeScore(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export function mergeSseCandidate<T extends { id: string }>(items: T[], incoming: T): T[] {
  const index = items.findIndex((item) => item.id === incoming.id)
  if (index < 0) return [...items, incoming]
  return items.map((item, current) => current === index ? { ...item, ...incoming } : item)
}
