# lolday-frontend

React + Vite + shadcn/ui SPA for lolday.

## Dev

```bash
pnpm install
pnpm dev
```

## Unit tests

```bash
pnpm test
```

## E2E

Requires the backend to be reachable on `http://localhost:8000` and credentials in env:

```bash
source ~/.lolday-secrets.env
export E2E_ADMIN_EMAIL=$ADMIN_EMAIL E2E_ADMIN_PASSWORD=$ADMIN_PASSWORD
pnpm dev &
pnpm test:e2e
```
