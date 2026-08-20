FROM python:3.11-slim

# Install Node.js and system dependencies
RUN apt-get update && apt-get install -y \
    nodejs npm curl tmux git jq \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency files
COPY package.json ./
RUN npm install --production 2>/dev/null || true

# Install Python packages
RUN pip install construct base58

# Copy project files
COPY . .

# Create .env if not exists
RUN cp .env.example .env 2>/dev/null || true

# Make scripts executable
RUN chmod +x run.sh always-on.sh android-install.sh pc-install.sh github-sync.sh start-bot.sh install.sh

EXPOSE 8765

CMD ["./run.sh", "--devnet", "--dry-run", "--full", "--budget-usd", "6", "--wallets", "5", "--auto", "--test-mode"]
