FROM node:22-alpine AS build
WORKDIR /web
COPY apps/portal-web/package.json ./
RUN npm install
COPY apps/portal-web/ ./
ARG VITE_API_BASE=http://127.0.0.1:8000
ENV VITE_API_BASE=$VITE_API_BASE
RUN npm run build

FROM nginx:1.27-alpine
COPY apps/portal-web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
