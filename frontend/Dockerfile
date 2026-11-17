# Node 22 LTS: same major as the frontend CI job; package.json engines >= 22.22.2.
FROM node:22-alpine AS base

WORKDIR /app
COPY package*.json ./
RUN npm ci

COPY . .

EXPOSE 3000
ENV NODE_ENV=development

CMD ["npm", "run", "dev"]
