import type { Problem } from './types';

/** Error thrown by the API layer; wraps an RFC 9457 problem document. */
export class ApiError extends Error {
  readonly status: number;
  readonly problem: Problem;
  constructor(problem: Problem) {
    super(problem.detail ? `${problem.title}: ${problem.detail}` : problem.title);
    this.name = 'ApiError';
    this.status = problem.status;
    this.problem = problem;
  }
  get code(): string | undefined {
    return this.problem.code;
  }
}

export async function toProblem(res: Response, body?: unknown): Promise<Problem> {
  let b = body;
  if (b === undefined) {
    try {
      b = await res.clone().json();
    } catch {
      b = undefined;
    }
  }
  if (b && typeof b === 'object' && 'title' in b) return { status: res.status, type: 'about:blank', ...(b as object) } as Problem;
  return { type: 'about:blank', title: res.statusText || `Request failed (${res.status})`, status: res.status };
}

export const isApiError = (e: unknown): e is ApiError => e instanceof ApiError;

/** Human message for a toast. */
export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.problem.detail ? `${e.problem.title}. ${e.problem.detail}` : e.problem.title;
  if (e instanceof TypeError) return 'Could not reach the API. Check the base URL in Settings → API access.';
  return e instanceof Error ? e.message : String(e);
}
