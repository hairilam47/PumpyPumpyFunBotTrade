#!/bin/bash
# terraform/templates/user_data.sh
set -e

exec > >(tee /var/log/user-data.log | logger -t user-data) 2>&1
echo "Starting user data script at $(date)"

# Update system
apt-get update
apt-get upgrade -y

# Install dependencies
apt-get install -y \
    apt-transport-https \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    git \
    build-essential \
    pkg-config \
    libssl-dev \
    unzip \
    jq

# Install Docker
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null

apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

systemctl start docker
systemctl enable docker

# Install Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
source $HOME/.cargo/env

# Install AWS CLI
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
./aws/install

# Create bot user
useradd -m -s /bin/bash pumpfun
usermod -aG docker pumpfun

# Clone and build bot
mkdir -p /opt/pumpfun
cd /opt/pumpfun
git clone https://github.com/your-repo/pumpfun-bot.git .

# Create environment file
cat > .env << EOF
ENVIRONMENT=${ENVIRONMENT}
DATABASE_URL=${DATABASE_URL}
REDIS_URL=${REDIS_URL}
HELIUS_API_KEY=${HELIUS_API_KEY}
QUICKNODE_API_KEY=${QUICKNODE_API_KEY}
WALLET_PRIVATE_KEY=${WALLET_PRIVATE_KEY}
MAX_TRADE_SIZE_SOL=${MAX_TRADE_SIZE_SOL}
SLIPPAGE_BPS=${SLIPPAGE_BPS}
AWS_REGION=${AWS_REGION}
JITO_BUNDLE_URL=${JITO_BUNDLE_URL}
EOF

chown -R pumpfun:pumpfun /opt/pumpfun

# Build Rust binary
sudo -u pumpfun bash -c "source $HOME/.cargo/env && cd /opt/pumpfun && cargo build --release"

# Setup systemd service
cat > /etc/systemd/system/pumpfun-bot.service << EOF
[Unit]
Description=PumpFun Trading Bot
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=pumpfun
WorkingDirectory=/opt/pumpfun
EnvironmentFile=/opt/pumpfun/.env
ExecStart=/opt/pumpfun/target/release/pumpfun-bot
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Setup CloudWatch agent
wget https://s3.amazonaws.com/amazoncloudwatch-agent/ubuntu/amd64/latest/amazon-cloudwatch-agent.deb
dpkg -i amazon-cloudwatch-agent.deb

cat > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json << EOF
{
  "agent": {
    "metrics_collection_interval": 60,
    "run_as_user": "root"
  },
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "/var/log/pumpfun-bot.log",
            "log_group_name": "${ENVIRONMENT}-pumpfun-bot",
            "log_stream_name": "{instance_id}"
          }
        ]
      }
    }
  }
}
EOF

/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
    -a fetch-config \
    -m ec2 \
    -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json \
    -s

# Start bot
systemctl daemon-reload
systemctl enable pumpfun-bot.service
systemctl start pumpfun-bot.service
systemctl enable amazon-cloudwatch-agent
systemctl start amazon-cloudwatch-agent

echo "User data script completed at $(date)"