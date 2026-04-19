// TEMPORARY STUB — replaced by Task 14 with real TanStack Query-backed hook.
// This exists now so Task 12 (Sidebar) can import it and typecheck.

export interface CurrentUserStub {
  email: string;
}

export function useAuth(): {
  currentUser: CurrentUserStub | null;
  logout: () => void;
} {
  return {
    currentUser: null,
    logout: () => {},
  };
}
