# Frontend Dockerfile
FROM node:20-alpine

WORKDIR /app

# Copy package files
COPY front/package*.json ./

# Install dependencies
RUN npm install

# Copy source code
COPY front/ ./

# Expose Vite dev server port
EXPOSE 5173

# Start dev server
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]