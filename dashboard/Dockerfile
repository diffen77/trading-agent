FROM node:20-alpine

WORKDIR /app

# Install dependencies
COPY package*.json ./
RUN npm install

# Copy app
COPY . .

# Build
RUN npm run build

# Run
EXPOSE 3000
CMD ["npm", "start"]
