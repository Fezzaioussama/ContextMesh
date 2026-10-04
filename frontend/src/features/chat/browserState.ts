export interface PendingAttempt {
  message: string;
  key: string;
}
const selectionKey = "contextmesh.selected-conversation";

export function readSelection(): string | null {
  try {
    return localStorage.getItem(selectionKey);
  } catch {
    return null;
  }
}

export function saveSelection(id: string): void {
  try {
    localStorage.setItem(selectionKey, id);
  } catch {
    // Browsers may disable local storage; server history still works.
  }
}

export function readAttempt(id: string): PendingAttempt | null {
  try {
    const stored = sessionStorage.getItem(`contextmesh.turn.${id}`);
    if (stored === null) return null;
    return JSON.parse(stored) as PendingAttempt;
  } catch {
    return null;
  }
}

export function saveAttempt(id: string, attempt: PendingAttempt): void {
  try {
    sessionStorage.setItem(`contextmesh.turn.${id}`, JSON.stringify(attempt));
  } catch {
    // The hook retains its key in memory when session storage is unavailable.
  }
}

export function clearAttempt(id: string): void {
  try {
    sessionStorage.removeItem(`contextmesh.turn.${id}`);
  } catch {
    // Storage availability must not turn a successful response into a failure.
  }
}
