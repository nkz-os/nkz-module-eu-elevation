# Multi-stage build for EU Elevation Module Frontend (local dev / standalone deploy)
# Stage 1: Build with Vite + Module Federation
FROM node:20-alpine AS builder

WORKDIR /app

# Copy package files from root
COPY package.json package-lock.json* ./

# Install dependencies
RUN npm install --registry=https://registry.npmjs.org/

# Copy all source files needed for build
COPY src/ ./src/
COPY index.html ./
COPY vite.config.ts ./
COPY tsconfig.json ./
COPY tsconfig.node.json ./
COPY tailwind.config.js ./
COPY postcss.config.js ./
COPY manifest.json ./

# Build the application
RUN npm run build

# Stage 2: Serve with Nginx
FROM nginx:alpine

# Copy built assets (includes remoteEntry.js)
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy custom nginx config for Module Federation
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

# Health endpoint
RUN echo "OK" > /usr/share/nginx/html/health

# Expose port
EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
