import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// Register the i18next instance globally so components calling useTranslation
// (StatusBadge, OpenInMlflowButton, …) get a real `t` function instead of
// crashing with NO_I18NEXT_INSTANCE / `i18n.exists is not a function`.
import "@/i18n";

afterEach(() => cleanup());
