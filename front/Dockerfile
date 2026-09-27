# Stage 1: Build
FROM node:20-alpine AS builder
WORKDIR /app
COPY front/package*.json ./
RUN npm ci --include=optional    # <-- ADD --include=optional
COPY front/ ./
RUN npm run build

# Stage 2: Serve with nginx
FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY front/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]