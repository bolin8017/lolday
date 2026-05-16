/**
 * D3.1 — helpers barrel.
 *
 * Existing callers `import { login } from "./helpers"` resolve to this
 * file (when the import has no extension, TS / playwright pick the
 * directory's index.ts). Page-object models live as siblings under
 * `./helpers/*.po.ts` and are imported directly via their filename.
 */
export {
  type DevPersona,
  type SeedCreds,
  login,
  loginAs,
  personaForWorker,
  seedCreds,
} from "./auth";
