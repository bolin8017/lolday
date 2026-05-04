# Mobile Responsive PR-1 — Foundation + Sidebar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hardcoded 240 px sidebar with shadcn/ui's Sidebar block (mobile drawer + desktop collapsible), wire a Light/Dark/System theme toggle, and migrate the icon set — all without breaking existing list pages.

**Architecture:** Drop-in shadcn primitives (`SidebarProvider`, `SidebarInset`, `Sidebar`) replace the hand-coded flex shell in `_authed.tsx`. A 30-line shadcn-Vite `ThemeProvider` sets `class="dark"` on `<html>` and persists choice in `localStorage`. New `useIsMobile` hook (matchMedia-based, shared across the codebase) feeds responsive switches. CSS gains `--sidebar-*` HSL tokens that flip with the theme; the old hardcoded `bg-slate-900` goes away.

**Tech Stack:** React 19, TypeScript 5.9, Tailwind 3.4, shadcn/ui (Radix primitives), vaul, lucide-react 1.14, react-i18next, vitest + @testing-library/react.

**Spec:** `docs/superpowers/specs/2026-05-04-mobile-responsive-redesign-design.md`

---

## File Structure

| Action | Path                                                    | Responsibility                                                       |
| ------ | ------------------------------------------------------- | -------------------------------------------------------------------- |
| Create | `frontend/src/hooks/useIsMobile.ts`                     | matchMedia hook, returns `boolean` for `< 768px`                     |
| Create | `frontend/src/components/ThemeProvider.tsx`             | Context + `localStorage` persistence + `<html class>` toggle         |
| Create | `frontend/src/components/ThemeToggle.tsx`               | DropdownMenu UI for Light / Dark / System                            |
| Create | `frontend/src/components/ui/sidebar.tsx`                | shadcn Sidebar block primitives (copied verbatim)                    |
| Create | `frontend/src/components/layout/AppSidebar.tsx`         | Lolday-specific nav with new icon set                                |
| Modify | `frontend/src/index.css`                                | Add `--sidebar-*` HSL tokens (light + dark blocks)                   |
| Modify | `frontend/tailwind.config.ts`                           | Map `bg-sidebar`, `text-sidebar-foreground`, etc. to the CSS vars    |
| Modify | `frontend/src/routes/_authed.tsx`                       | Wrap with `<ThemeProvider>` + `<SidebarProvider>` + `<SidebarInset>` |
| Modify | `frontend/src/components/layout/TopBar.tsx`             | Inject `<SidebarTrigger>` (left) + `<ThemeToggle>` (right)           |
| Modify | `frontend/src/i18n/en.json` + `zh-TW.json`              | Add `nav.menu`, `theme.{light,dark,system,toggle}` keys              |
| Delete | `frontend/src/components/layout/Sidebar.tsx`            | Replaced by `AppSidebar`                                             |
| Test   | `frontend/tests/unit/hooks/useIsMobile.test.ts`         | Unit                                                                 |
| Test   | `frontend/tests/unit/components/ThemeProvider.test.tsx` | Unit                                                                 |
| Test   | `frontend/tests/unit/components/ThemeToggle.test.tsx`   | Unit                                                                 |
| Test   | `frontend/tests/unit/components/AppSidebar.test.tsx`    | Unit (renders nav + admin gate)                                      |

---

### Task 1: Branch + worktree setup

**Files:** none (git only)

- [ ] **Step 1: Create feature branch from main**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git fetch origin
git switch main
git pull --ff-only
git switch -c feat/mobile-responsive-pr1-foundation-sidebar
```

- [ ] **Step 2: Verify clean working tree**

Run: `git status`
Expected: `nothing to commit, working tree clean` and `On branch feat/mobile-responsive-pr1-foundation-sidebar`

---

### Task 2: useIsMobile hook (TDD)

**Files:**

- Create: `frontend/src/hooks/useIsMobile.ts`
- Test: `frontend/tests/unit/hooks/useIsMobile.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// frontend/tests/unit/hooks/useIsMobile.test.ts
import { renderHook, act } from "@testing-library/react";
import { describe, expect, it, beforeEach, vi } from "vitest";
import { useIsMobile } from "@/hooks/useIsMobile";

type Listener = (e: MediaQueryListEvent) => void;

function mockMatchMedia(initialMatches: boolean) {
  const listeners: Listener[] = [];
  const mql = {
    matches: initialMatches,
    media: "(max-width: 767px)",
    addEventListener: (_: string, cb: Listener) => listeners.push(cb),
    removeEventListener: (_: string, cb: Listener) => {
      const i = listeners.indexOf(cb);
      if (i >= 0) listeners.splice(i, 1);
    },
    dispatchEvent: () => true,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
  } as unknown as MediaQueryList;
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockReturnValue(mql),
  });
  return {
    fire(matches: boolean) {
      mql.matches = matches;
      listeners.forEach((cb) => cb({ matches } as MediaQueryListEvent));
    },
  };
}

describe("useIsMobile", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("returns true when viewport matches mobile media query", () => {
    mockMatchMedia(true);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(true);
  });

  it("returns false when viewport does not match", () => {
    mockMatchMedia(false);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(false);
  });

  it("re-renders when viewport crosses the breakpoint", () => {
    const ctrl = mockMatchMedia(false);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(false);
    act(() => ctrl.fire(true));
    expect(result.current).toBe(true);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd frontend
pnpm test useIsMobile
```

Expected: FAIL with `Cannot find module '@/hooks/useIsMobile'`.

- [ ] **Step 3: Implement the hook**

```ts
// frontend/src/hooks/useIsMobile.ts
import { useEffect, useState } from "react";

const MOBILE_QUERY = "(max-width: 767px)";

/**
 * Returns true when the viewport matches the mobile breakpoint
 * (`< 768px`). Subscribes to `matchMedia` change events so consumers
 * re-render across the breakpoint. Aligns with shadcn/ui's Sidebar
 * block, which uses the same threshold internally.
 */
export function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState(() =>
    typeof window === "undefined"
      ? false
      : window.matchMedia(MOBILE_QUERY).matches,
  );

  useEffect(() => {
    const mql = window.matchMedia(MOBILE_QUERY);
    const onChange = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    mql.addEventListener("change", onChange);
    setIsMobile(mql.matches); // sync after hydration
    return () => mql.removeEventListener("change", onChange);
  }, []);

  return isMobile;
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pnpm test useIsMobile
```

Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/hooks/useIsMobile.ts tests/unit/hooks/useIsMobile.test.ts
git commit -m "feat(frontend): add useIsMobile matchMedia hook"
```

---

### Task 3: ThemeProvider (TDD)

**Files:**

- Create: `frontend/src/components/ThemeProvider.tsx`
- Test: `frontend/tests/unit/components/ThemeProvider.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/tests/unit/components/ThemeProvider.test.tsx
import { render, act } from "@testing-library/react";
import { describe, expect, it, beforeEach, vi } from "vitest";
import { ThemeProvider, useTheme } from "@/components/ThemeProvider";

const STORAGE_KEY = "lolday-theme";

function ThemeReporter() {
  const { theme, setTheme } = useTheme();
  return (
    <div>
      <span data-testid="t">{theme}</span>
      <button data-testid="dark" onClick={() => setTheme("dark")}>
        dark
      </button>
      <button data-testid="light" onClick={() => setTheme("light")}>
        light
      </button>
      <button data-testid="system" onClick={() => setTheme("system")}>
        system
      </button>
    </div>
  );
}

describe("ThemeProvider", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove("light", "dark");
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn().mockImplementation(() => ({
        matches: false,
        media: "",
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: () => true,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
      })),
    });
  });

  it("uses default theme when localStorage is empty", () => {
    const { getByTestId } = render(
      <ThemeProvider defaultTheme="system" storageKey={STORAGE_KEY}>
        <ThemeReporter />
      </ThemeProvider>,
    );
    expect(getByTestId("t").textContent).toBe("system");
  });

  it("loads persisted theme from localStorage", () => {
    localStorage.setItem(STORAGE_KEY, "dark");
    const { getByTestId } = render(
      <ThemeProvider defaultTheme="system" storageKey={STORAGE_KEY}>
        <ThemeReporter />
      </ThemeProvider>,
    );
    expect(getByTestId("t").textContent).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });

  it("setTheme writes localStorage and toggles <html> class", () => {
    const { getByTestId } = render(
      <ThemeProvider defaultTheme="light" storageKey={STORAGE_KEY}>
        <ThemeReporter />
      </ThemeProvider>,
    );
    act(() => getByTestId("dark").click());
    expect(localStorage.getItem(STORAGE_KEY)).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(document.documentElement.classList.contains("light")).toBe(false);

    act(() => getByTestId("light").click());
    expect(document.documentElement.classList.contains("light")).toBe(true);
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pnpm test ThemeProvider
```

Expected: FAIL with `Cannot find module '@/components/ThemeProvider'`.

- [ ] **Step 3: Implement ThemeProvider**

```tsx
// frontend/src/components/ThemeProvider.tsx
import { createContext, useContext, useEffect, useState } from "react";

export type Theme = "light" | "dark" | "system";

interface ThemeContextValue {
  theme: Theme;
  setTheme: (t: Theme) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

interface Props {
  children: React.ReactNode;
  defaultTheme?: Theme;
  storageKey?: string;
}

export function ThemeProvider({
  children,
  defaultTheme = "system",
  storageKey = "lolday-theme",
}: Props) {
  const [theme, setThemeState] = useState<Theme>(() => {
    if (typeof window === "undefined") return defaultTheme;
    return (localStorage.getItem(storageKey) as Theme) || defaultTheme;
  });

  useEffect(() => {
    const root = document.documentElement;
    root.classList.remove("light", "dark");
    if (theme === "system") {
      const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      root.classList.add(dark ? "dark" : "light");
      return;
    }
    root.classList.add(theme);
  }, [theme]);

  // System mode tracks OS preference live.
  useEffect(() => {
    if (theme !== "system") return;
    const mql = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = (e: MediaQueryListEvent) => {
      const root = document.documentElement;
      root.classList.remove("light", "dark");
      root.classList.add(e.matches ? "dark" : "light");
    };
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, [theme]);

  const setTheme = (next: Theme) => {
    localStorage.setItem(storageKey, next);
    setThemeState(next);
  };

  return (
    <ThemeContext.Provider value={{ theme, setTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within <ThemeProvider>");
  return ctx;
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pnpm test ThemeProvider
```

Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/components/ThemeProvider.tsx tests/unit/components/ThemeProvider.test.tsx
git commit -m "feat(frontend): add ThemeProvider with localStorage persistence"
```

---

### Task 4: ThemeToggle (component test)

**Files:**

- Create: `frontend/src/components/ThemeToggle.tsx`
- Test: `frontend/tests/unit/components/ThemeToggle.test.tsx`

- [ ] **Step 1: Add i18n keys (both locales)**

```bash
# verify the existing structure first
grep -n '"theme"' src/i18n/en.json src/i18n/zh-TW.json
```

Expected: no results (keys do not yet exist).

Edit `src/i18n/en.json`:

1. Append a new top-level `"theme"` object (before the closing `}`):

   ```json
     "theme": {
       "toggle": "Toggle theme",
       "light": "Light",
       "dark": "Dark",
       "system": "System"
     }
   ```

2. Inside the existing `"nav"` block, add a `"menu"` key:

   ```json
     "menu": "Open menu"
   ```

Do **not** copy a `"nav": { ... existing keys ... }` placeholder verbatim — that produces invalid JSON. Merge the `"menu"` key into the actual `"nav"` object.

Edit `src/i18n/zh-TW.json` analogously:

1. New top-level `"theme"`:

   ```json
     "theme": {
       "toggle": "切換主題",
       "light": "淺色",
       "dark": "深色",
       "system": "跟隨系統"
     }
   ```

2. Inside existing `"nav"`, add `"menu": "開啟選單"`.

- [ ] **Step 2: Extend `frontend/tests/setup.ts` with the Radix v2 PointerEvent shim**

shadcn `DropdownMenu` is a Radix primitive that listens on `pointerdown`. jsdom does not implement `PointerEvent`, `setPointerCapture`, `hasPointerCapture`, `releasePointerCapture`, or `scrollIntoView`, so any test that opens a Radix Dropdown / Dialog / Popover will hang on `findByRole("menu")` — even with `userEvent`. The fix is a global jsdom shim. Append to `frontend/tests/setup.ts`:

```ts
// Radix UI primitives use PointerEvent + pointer capture APIs that jsdom does not
// implement. See radix-ui/primitives#1342.
window.PointerEvent = MouseEvent as typeof PointerEvent;
window.HTMLElement.prototype.hasPointerCapture = () => false;
window.HTMLElement.prototype.releasePointerCapture = () => {};
window.HTMLElement.prototype.setPointerCapture = () => {};
window.HTMLElement.prototype.scrollIntoView = () => {};

// jsdom does not implement window.matchMedia. Stubbed globally so any component
// (Sidebar block, ThemeProvider, useIsMobile, …) that reads matchMedia on mount
// does not throw "not a function". Individual tests may override with
// Object.defineProperty(window, "matchMedia", { configurable: true, value: … }).
Object.defineProperty(window, "matchMedia", {
  configurable: true,
  value: vi.fn().mockReturnValue({
    matches: false,
    media: "",
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: () => true,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
  }),
});
```

This shim is a one-time setup; subsequent Radix tests reuse it. **Do not use `fireEvent.click` on a Radix DropdownMenu / Dialog / Popover trigger** — it dispatches a synthetic click that bypasses pointerdown and the menu never opens. Always use `userEvent.setup()` + `await user.click(...)`.

- [ ] **Step 3: Write the failing test**

```tsx
// frontend/tests/unit/components/ThemeToggle.test.tsx
import { render, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, beforeEach } from "vitest";
import { ThemeProvider } from "@/components/ThemeProvider";
import { ThemeToggle } from "@/components/ThemeToggle";

beforeEach(() => {
  localStorage.clear();
  document.documentElement.classList.remove("light", "dark");
});

describe("ThemeToggle", () => {
  it("renders three theme options when opened", async () => {
    const user = userEvent.setup();
    const { getByLabelText } = render(
      <ThemeProvider defaultTheme="light">
        <ThemeToggle />
      </ThemeProvider>,
    );
    await user.click(getByLabelText(/toggle theme|切換主題/i));
    const menu = await within(document.body).findByRole("menu");
    expect(within(menu).getByText(/light|淺色/i)).toBeInTheDocument();
    expect(within(menu).getByText(/dark|深色/i)).toBeInTheDocument();
    expect(within(menu).getByText(/system|跟隨系統/i)).toBeInTheDocument();
  });

  it("clicking 'Dark' adds the dark class to <html>", async () => {
    const user = userEvent.setup();
    const { getByLabelText } = render(
      <ThemeProvider defaultTheme="light">
        <ThemeToggle />
      </ThemeProvider>,
    );
    await user.click(getByLabelText(/toggle theme|切換主題/i));
    const dark = await within(document.body).findByText(/^dark$|^深色$/i);
    await user.click(dark);
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });
});
```

- [ ] **Step 4: Run test to verify it fails**

```bash
pnpm test ThemeToggle
```

Expected: FAIL with `Cannot find module '@/components/ThemeToggle'`.

- [ ] **Step 5: Implement ThemeToggle**

```tsx
// frontend/src/components/ThemeToggle.tsx
import { Moon, Sun, Monitor } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useTheme } from "@/components/ThemeProvider";

export function ThemeToggle() {
  const { t } = useTranslation();
  const { setTheme } = useTheme();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label={t("theme.toggle")}>
          <Sun className="h-5 w-5 rotate-0 scale-100 transition-all dark:-rotate-90 dark:scale-0" />
          <Moon className="absolute h-5 w-5 rotate-90 scale-0 transition-all dark:rotate-0 dark:scale-100" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onClick={() => setTheme("light")}>
          <Sun className="mr-2 h-4 w-4" />
          {t("theme.light")}
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => setTheme("dark")}>
          <Moon className="mr-2 h-4 w-4" />
          {t("theme.dark")}
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => setTheme("system")}>
          <Monitor className="mr-2 h-4 w-4" />
          {t("theme.system")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
```

- [ ] **Step 6: Run test to verify it passes**

```bash
pnpm test ThemeToggle
```

Expected: 2 tests pass.

- [ ] **Step 7: Commit**

```bash
git add tests/setup.ts \
        src/components/ThemeToggle.tsx tests/unit/components/ThemeToggle.test.tsx \
        src/i18n/en.json src/i18n/zh-TW.json
git commit -m "feat(frontend): add ThemeToggle dropdown + i18n keys + Radix shim"
```

---

### Task 5: Sidebar CSS tokens + Tailwind mapping

**Files:**

- Modify: `frontend/src/index.css`
- Modify: `frontend/tailwind.config.ts`

- [ ] **Step 1: Append `--sidebar-*` tokens to index.css**

Inside the existing `:root { ... }` block (right before the closing `}`), insert:

```css
--sidebar: 0 0% 98%;
--sidebar-foreground: 240 5.3% 26.1%;
--sidebar-primary: 240 5.9% 10%;
--sidebar-primary-foreground: 0 0% 98%;
--sidebar-accent: 240 4.8% 95.9%;
--sidebar-accent-foreground: 240 5.9% 10%;
--sidebar-border: 220 13% 91%;
--sidebar-ring: 217.2 91.2% 59.8%;
```

Inside the existing `.dark { ... }` block (right before the closing `}`), insert:

```css
--sidebar: 240 5.9% 10%;
--sidebar-foreground: 240 4.8% 95.9%;
--sidebar-primary: 224.3 76.3% 48%;
--sidebar-primary-foreground: 0 0% 100%;
--sidebar-accent: 240 3.7% 15.9%;
--sidebar-accent-foreground: 240 4.8% 95.9%;
--sidebar-border: 240 3.7% 15.9%;
--sidebar-ring: 217.2 91.2% 59.8%;
```

- [ ] **Step 2: Map sidebar tokens in tailwind.config.ts**

Inside `theme.extend.colors`, after the existing `card: { ... }` entry, insert:

```ts
        sidebar: {
          DEFAULT: "hsl(var(--sidebar))",
          foreground: "hsl(var(--sidebar-foreground))",
          primary: "hsl(var(--sidebar-primary))",
          "primary-foreground": "hsl(var(--sidebar-primary-foreground))",
          accent: "hsl(var(--sidebar-accent))",
          "accent-foreground": "hsl(var(--sidebar-accent-foreground))",
          border: "hsl(var(--sidebar-border))",
          ring: "hsl(var(--sidebar-ring))",
        },
```

- [ ] **Step 3: Verify Tailwind compiles**

```bash
pnpm typecheck
```

Expected: no errors.

```bash
pnpm test
```

Expected: still passes (no test changed).

- [ ] **Step 4: Commit**

```bash
git add src/index.css tailwind.config.ts
git commit -m "feat(frontend): add --sidebar-* CSS tokens for theme-aware sidebar"
```

---

### Task 6: Install shadcn Sidebar block

**Files:**

- Create: `frontend/src/components/ui/sidebar.tsx` (and any other primitives shadcn pulls in: `tooltip.tsx`, `skeleton.tsx`, `separator.tsx` may already exist)

- [ ] **Step 1: Run shadcn add**

```bash
cd frontend
pnpm dlx shadcn@latest add sidebar
```

Expected: prompts to confirm overwriting any existing primitives. Accept overwrite for API-compatible peers (`tooltip`, `skeleton`, `separator`, `sheet`) — shadcn keeps these stable.

**⚠️ Two collisions the CLI silently produces — reconcile BEFORE committing:**

1. **`src/hooks/use-mobile.tsx`** — the CLI drops a hook here that duplicates the project's `src/hooks/useIsMobile.ts` (created in Task 2). Keep the project hook; delete the CLI artifact:

   ```bash
   rm -f src/hooks/use-mobile.tsx
   ```

   Verify `src/components/ui/sidebar.tsx` imports from `@/hooks/useIsMobile`, not the deleted file. If shadcn's bundled `sidebar.tsx` imports `./use-mobile`, hand-edit to `import { useIsMobile } from "@/hooks/useIsMobile";`.

2. **`--sidebar-background` token name** — older shadcn templates (and some bundled blocks) use `--sidebar-background` as the DEFAULT colour. The project's `index.css` (Task 5) and `tailwind.config.ts` (Task 5) declare `--sidebar` (no `-background` suffix). After running the CLI, scan both files:

   ```bash
   grep -nE "sidebar-background" src/index.css tailwind.config.ts
   ```

   Expected: zero matches. If present, replace `--sidebar-background` with `--sidebar` in `tailwind.config.ts` AND any CSS rule the CLI may have appended. The mismatch is silent — Tailwind's `bg-sidebar` resolves to `hsl(var(--sidebar-background))`, fails to substitute, falls back to transparent. jsdom does not catch this; only browser visual verification does. Project memory `project_shadcn_cli_collisions.md` documents this incident.

Run `git status` after the CLI completes and reconcile both before moving on.

If the CLI fails (offline / network / version mismatch), fall back to manual copy: paste `sidebar.tsx` from <https://ui.shadcn.com/docs/components/sidebar> into `src/components/ui/sidebar.tsx`. The block ships ~600 lines of `Sidebar`, `SidebarProvider`, `SidebarTrigger`, `SidebarInset`, `SidebarMenu*`, etc.

- [ ] **Step 2: Verify the block compiles**

```bash
pnpm typecheck
```

Expected: no errors.

- [ ] **Step 3: Run lint to catch any new violations**

```bash
pnpm lint
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add src/components/ui/sidebar.tsx
# include any peer files shadcn updated
git diff --stat
git add <any other changed src/components/ui/*.tsx>
git commit -m "feat(frontend): install shadcn/ui Sidebar block"
```

---

### Task 7: AppSidebar component

**Files:**

- Create: `frontend/src/components/layout/AppSidebar.tsx`
- Test: `frontend/tests/unit/components/AppSidebar.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/tests/unit/components/AppSidebar.test.tsx
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { SidebarProvider } from "@/components/ui/sidebar";
import { AppSidebar } from "@/components/layout/AppSidebar";

vi.mock("@/hooks/useAuth", () => ({
  useAuth: () => ({
    currentUser: { email: "lab@test", role: "admin" },
    isLoading: false,
    isUnauthenticated: false,
    logout: vi.fn(),
  }),
}));

function renderSidebar() {
  return render(
    <MemoryRouter>
      <SidebarProvider>
        <AppSidebar />
      </SidebarProvider>
    </MemoryRouter>,
  );
}

describe("AppSidebar", () => {
  it("renders the five primary nav items", () => {
    const { getByText } = renderSidebar();
    expect(getByText(/detectors|偵測器/i)).toBeInTheDocument();
    expect(getByText(/datasets|資料集/i)).toBeInTheDocument();
    expect(getByText(/jobs|工作/i)).toBeInTheDocument();
    expect(getByText(/runs|執行紀錄/i)).toBeInTheDocument();
    expect(getByText(/models|模型/i)).toBeInTheDocument();
  });

  it("renders the admin link when role is admin", () => {
    const { getByText } = renderSidebar();
    expect(getByText(/admin|管理/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pnpm test AppSidebar
```

Expected: FAIL with `Cannot find module '@/components/layout/AppSidebar'`.

- [ ] **Step 3: Implement AppSidebar**

```tsx
// frontend/src/components/layout/AppSidebar.tsx
import { NavLink } from "react-router";
import { useTranslation } from "react-i18next";
import {
  Boxes,
  Database,
  Play,
  FlaskConical,
  Layers,
  UserCog,
  User as UserIcon,
  LogOut,
} from "lucide-react";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { useAuth } from "@/hooks/useAuth";

const NAV_ITEMS = [
  { to: "/detectors", icon: Boxes, labelKey: "nav.detectors" },
  { to: "/datasets", icon: Database, labelKey: "nav.datasets" },
  { to: "/jobs", icon: Play, labelKey: "nav.jobs" },
  { to: "/runs", icon: FlaskConical, labelKey: "nav.runs" },
  { to: "/models", icon: Layers, labelKey: "nav.models" },
] as const;

export function AppSidebar() {
  const { t } = useTranslation();
  const { currentUser, logout } = useAuth();

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <div className="px-2 py-1.5 text-lg font-semibold text-primary">
          {t("app.name")}
        </div>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              {NAV_ITEMS.map(({ to, icon: Icon, labelKey }) => (
                <SidebarMenuItem key={to}>
                  <SidebarMenuButton asChild tooltip={t(labelKey)}>
                    <NavLink to={to}>
                      <Icon />
                      <span>{t(labelKey)}</span>
                    </NavLink>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
              {currentUser?.role === "admin" && (
                <SidebarMenuItem>
                  <SidebarMenuButton asChild tooltip={t("nav.admin")}>
                    <NavLink to="/admin/users">
                      <UserCog />
                      <span>{t("nav.admin")}</span>
                    </NavLink>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              )}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton asChild tooltip={currentUser?.email ?? "—"}>
              <NavLink to="/profile">
                <UserIcon />
                <span className="truncate">{currentUser?.email ?? "—"}</span>
              </NavLink>
            </SidebarMenuButton>
          </SidebarMenuItem>
          <SidebarMenuItem>
            <SidebarMenuButton
              onClick={() => logout()}
              tooltip={t("nav.logout")}
            >
              <LogOut />
              <span>{t("nav.logout")}</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
        <p className="px-2 pt-1 text-[10px] text-muted-foreground">
          v{import.meta.env.VITE_APP_VERSION}
        </p>
      </SidebarFooter>
    </Sidebar>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pnpm test AppSidebar
```

Expected: 2 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/components/layout/AppSidebar.tsx tests/unit/components/AppSidebar.test.tsx
git commit -m "feat(frontend): add AppSidebar with new icon set"
```

---

### Task 8: Wire `_authed.tsx` to ThemeProvider + SidebarProvider

**Files:**

- Modify: `frontend/src/routes/_authed.tsx`

- [ ] **Step 1: Read the current shell**

```bash
cat src/routes/_authed.tsx
```

- [ ] **Step 2: Replace the shell**

Replace the `return (...)` block at the bottom (currently the `<div className="flex h-screen overflow-hidden">` tree) with:

```tsx
return (
  <ThemeProvider defaultTheme="system" storageKey="lolday-theme">
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset>
        <TopBar />
        <main className="flex-1 overflow-y-auto bg-background p-4 md:p-6">
          <Outlet />
        </main>
      </SidebarInset>
    </SidebarProvider>
  </ThemeProvider>
);
```

Update the imports at the top of the file:

```tsx
import { Outlet } from "react-router";
import { TopBar } from "@/components/layout/TopBar";
import { AppSidebar } from "@/components/layout/AppSidebar";
import { SidebarProvider, SidebarInset } from "@/components/ui/sidebar";
import { ThemeProvider } from "@/components/ThemeProvider";
import { useAuth } from "@/hooks/useAuth";
```

Remove the now-unused `import { Sidebar } from "@/components/layout/Sidebar"`.

- [ ] **Step 3: Typecheck**

```bash
pnpm typecheck
```

Expected: no errors.

- [ ] **Step 4: Run dev server and visually verify**

```bash
pnpm dev
```

Open <http://localhost:5173/detectors> in a desktop browser:

- Sidebar visible at 240 px on the left
- Click the rail toggle (or hit `Ctrl+B`) → sidebar collapses to icon-only
- Reload page → preference persists (cookie)

Resize the window below 768 px:

- Sidebar disappears
- TopBar shows hamburger (next step adds it; expect "broken" layout for now)

Stop dev server with Ctrl+C.

- [ ] **Step 5: Commit**

```bash
git add src/routes/_authed.tsx
git commit -m "feat(frontend): swap _authed shell to SidebarProvider + ThemeProvider"
```

---

### Task 9: Update TopBar — SidebarTrigger + ThemeToggle

**Files:**

- Modify: `frontend/src/components/layout/TopBar.tsx`

- [ ] **Step 1: Replace TopBar entirely**

Overwrite `frontend/src/components/layout/TopBar.tsx` with:

```tsx
import { Breadcrumb } from "./Breadcrumb";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Separator } from "@/components/ui/separator";

export function TopBar() {
  return (
    <header className="sticky top-0 z-10 flex h-14 shrink-0 items-center gap-2 border-b bg-card px-4 md:px-6">
      <SidebarTrigger className="-ml-1" />
      <Separator orientation="vertical" className="mx-2 h-4" />
      <div className="flex-1 min-w-0">
        <Breadcrumb />
      </div>
      <ThemeToggle />
    </header>
  );
}
```

- [ ] **Step 2: Typecheck + lint**

```bash
pnpm typecheck && pnpm lint
```

Expected: no errors.

- [ ] **Step 3: Visual verification**

```bash
pnpm dev
```

Confirm:

- Desktop ≥ 768 px: hamburger toggle on the left, Breadcrumb in the middle, theme toggle on the right.
- Mobile (devtools 393 px): hamburger opens a drawer from the left; clicking nav items navigates and closes the drawer; ESC closes it.
- Theme toggle: pick Dark → page colors invert; reload → still dark.
- Theme toggle: pick System → switch OS theme via devtools `prefers-color-scheme` emulation → page follows.

Stop dev server.

- [ ] **Step 4: Commit**

```bash
git add src/components/layout/TopBar.tsx
git commit -m "feat(frontend): add SidebarTrigger + ThemeToggle to TopBar"
```

---

### Task 10: Delete the old Sidebar.tsx

**Files:**

- Delete: `frontend/src/components/layout/Sidebar.tsx`

- [ ] **Step 1: Confirm no remaining imports**

```bash
grep -rnE "components/layout/Sidebar\b" src tests
```

Expected: no results. The single word-boundary pattern catches static imports (`from "@/components/layout/Sidebar"`), default imports, dynamic `import("@/components/layout/Sidebar")`, and `lazy(() => import(...))` references in one pass.

- [ ] **Step 2: Delete the file**

```bash
rm src/components/layout/Sidebar.tsx
```

- [ ] **Step 3: Typecheck + tests**

```bash
pnpm typecheck && pnpm test
```

Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add -A src/components/layout/
git commit -m "chore(frontend): remove obsolete Sidebar.tsx"
```

---

### Task 11: Run the full pre-flight

**Files:** none

- [ ] **Step 1: Lint, typecheck, tests**

```bash
cd frontend
pnpm format:check
pnpm lint
pnpm typecheck
pnpm test
```

Expected: each command exits 0.

- [ ] **Step 2: Run pre-commit on the diff**

```bash
cd ..
pre-commit run --files $(git diff --name-only main)
```

Expected: all hooks pass.

- [ ] **Step 3: Run existing E2E (optional, if E2E env reachable)**

```bash
cd frontend
pnpm playwright test --project=chromium
```

Expected: existing E2E pass against the deployed cluster (PR-1 must not regress desktop flows). If the E2E env is unavailable, document this and rely on the desktop visual checks already done.

- [ ] **Step 4: Manual acceptance checklist (`docs/superpowers/specs/2026-05-04-mobile-responsive-redesign-design.md` §5 PR-1)**

Open `pnpm dev` and verify each acceptance criterion against the running app:

- [ ] ≥ 768 px: sidebar at 240 px expanded; toggle to 56 px icon-only; reload preserves state.
- [ ] < 768 px: sidebar hidden; hamburger in TopBar opens drawer; ESC or overlay click closes.
- [ ] Theme toggle has Light / Dark / System; selection persists across reload.
- [ ] System mode follows OS `prefers-color-scheme` live (devtools emulation).
- [ ] New icons readable at 22 px in icon-only mode; no two icons confuse.
- [ ] Existing desktop list pages still render (Detectors / Datasets / Jobs / Runs / Models / Admin / Profile) — content untouched, only the chrome changed.

---

### Task 12: Push branch + open PR

**Files:** none

- [ ] **Step 1: Push**

```bash
git push -u origin feat/mobile-responsive-pr1-foundation-sidebar
```

- [ ] **Step 2: Open PR**

```bash
gh pr create \
  --title "feat(frontend): mobile responsive PR-1 — foundation + sidebar" \
  --body "$(cat <<'EOF'
## Summary

PR-1 of the mobile-first responsive redesign. Replaces the hardcoded 240 px sidebar with shadcn/ui's Sidebar block (mobile drawer + desktop collapsible icon-only), adds Light / Dark / System theme toggle, and migrates the icon set.

- Spec: `docs/superpowers/specs/2026-05-04-mobile-responsive-redesign-design.md`
- Plan: `docs/superpowers/plans/2026-05-04-mobile-responsive-pr1-foundation-sidebar.md`

## What changes for users

- Mobile (< 768 px): hamburger in TopBar opens a drawer with full nav.
- Desktop (≥ 768 px): sidebar collapses to icon-only via the rail toggle; choice persists across reloads.
- TopBar gains a Light / Dark / System theme toggle; selection persists in `localStorage`.
- Sidebar icons updated: Boxes (Detectors), Database (Datasets), FlaskConical (Runs), Layers (Models), UserCog (Admin).

## Test plan

- [x] `pnpm format:check && pnpm lint && pnpm typecheck && pnpm test` green.
- [x] Desktop visual: sidebar collapse / theme toggle / OS-preference live update.
- [x] Mobile (devtools 393 px): drawer open / close / nav.
- [ ] Desktop Playwright E2E green against cluster (run after merge if env unavailable locally).

## Out of scope (later PRs)

- DataTable card-mode on mobile — PR-2.
- Detail / forms / charts mobile fixes — PR-3.
- Mobile E2E project (iPhone 13 mini, Pixel 5) — PR-4.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Capture PR URL**

`gh pr view --json url -q .url`

Paste the URL into your worklog.

---

## Self-Review

**Spec coverage check (against §5 PR-1):**

| Spec requirement                                 | Plan task                                  |
| ------------------------------------------------ | ------------------------------------------ |
| Create `ui/sidebar.tsx`                          | Task 6                                     |
| Create `layout/AppSidebar.tsx`                   | Task 7                                     |
| Create `ThemeProvider.tsx`                       | Task 3                                     |
| Create `ThemeToggle.tsx`                         | Task 4                                     |
| Create `hooks/useIsMobile.ts`                    | Task 2                                     |
| Modify `index.css`                               | Task 5                                     |
| Modify `_authed.tsx`                             | Task 8                                     |
| Modify `TopBar.tsx`                              | Task 9                                     |
| Delete `layout/Sidebar.tsx`                      | Task 10                                    |
| Sidebar 240 px ↔ 56 px on desktop, persists      | Task 8 manual + Task 11 acceptance         |
| Sidebar drawer on mobile                         | Task 9 manual + Task 11 acceptance         |
| Theme toggle 3 options + persistence             | Task 4 + Task 11 acceptance                |
| System follows `prefers-color-scheme` live       | Task 3 implementation + Task 11 acceptance |
| New icons readable at 22 px                      | Task 7 + Task 11 acceptance                |
| `pnpm typecheck && pnpm lint && pnpm test` green | Task 11                                    |
| Existing Playwright E2E green                    | Task 11 step 3                             |

No gaps.

**Placeholder scan:** No `TBD`, `TODO`, or "implement later" in the plan. Each step shows the actual code or command.

**Type consistency:**

- `Theme = "light" | "dark" | "system"` — declared in Task 3, used in Task 4 callbacks and tests.
- `useIsMobile(): boolean` — Task 2; not directly consumed in PR-1 (used internally by shadcn Sidebar block which calls its own copy of the hook); kept for PR-2's DataTable.
- `AppSidebar` — exported as named export; matches import in Task 8.
- i18n keys (`theme.light`, `theme.dark`, `theme.system`, `theme.toggle`, `nav.menu`) consistent across Tasks 4 and 9.

No inconsistencies.
