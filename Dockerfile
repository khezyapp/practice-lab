# syntax=docker/dockerfile:1

# =============================================================================
# TaskFlow docs — build the VitePress site, then serve the static output with
# nginx. The final image contains no Node.js and no source Markdown.
#
#   docker build -t practice-lab-docs .
#   docker run --rm -p 8080:80 practice-lab-docs
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1 — build the site
# -----------------------------------------------------------------------------
FROM node:22-alpine AS build

WORKDIR /app

# VitePress's `lastUpdated` shells out to `git`; without the binary the build fails
# outright rather than degrading. With git present but no `.git` directory (which is
# what happens here, since `.git` is not part of the build context) it simply omits the
# dates. This is the build stage only, so it costs nothing in the final image.
RUN apk add --no-cache git

# Dependencies first: this layer is cached until the lockfile itself changes.
COPY package.json package-lock.json ./
RUN npm ci

# Only the site sources are needed. The build writes to docs/.vitepress/dist.
COPY docs ./docs
RUN npm run docs:build

# -----------------------------------------------------------------------------
# Stage 2 — serve it
# -----------------------------------------------------------------------------
FROM nginx:alpine AS runtime

COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/docs/.vitepress/dist /usr/share/nginx/html

EXPOSE 80

# 127.0.0.1, not localhost: inside this image `localhost` resolves to ::1 first, and
# nginx listens on IPv4 only, so the check would report the container as unhealthy.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1

CMD ["nginx", "-g", "daemon off;"]
