# ---- build stage ----
FROM node:22-alpine@sha256:8ea2348b068a9544dae7317b4f3aafcdc032df1647bb7d768a05a5cad1a7683f AS build
# pnpm >=11 runs `runDepsStatusCheck` before `pnpm run <script>` and refuses
# to proceed in non-CI environments without an explicit affirmation. GHA
# sets CI=true automatically, but local `docker build` does not — pin the
# value so both environments share the same code path.
ENV CI=true
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm run build

# ---- serve stage ----
# nginxinc/nginx-unprivileged is pre-configured for non-root + listen 8080
# + pid/cache/tmp under /tmp. Works with readOnlyRootFilesystem.
FROM nginxinc/nginx-unprivileged:1.29-alpine@sha256:0c79d56aee561a1d81c63f00eee5fb5fe29279560cdc55e91425133104c7fbe6
COPY --from=build /app/dist /usr/share/nginx/html
# F-sourcemaps: strip *.map from runtime image. The build stage retains
# them so CI can extract via `docker buildx build --target=build
# --output type=local,dest=...`.
USER root
RUN find /usr/share/nginx/html -name '*.map' -delete
USER 101
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s \
  CMD wget -q --spider http://127.0.0.1:8080/healthz || exit 1
