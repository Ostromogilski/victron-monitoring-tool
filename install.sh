#!/bin/bash

# Define repository URL and installation directory
REPO_URL="https://github.com/Ostromogilski/victron-monitoring-tool.git"
INSTALL_DIR="/opt/victron-monitoring-tool"

# Ensure the script is running with sudo
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (e.g., sudo bash <script>)"
  exit 1
fi

# Check for git installation
if ! command -v git &> /dev/null
then
    echo "Error: git could not be found. Please install git to proceed."
    exit 1
fi

# Clone the repository
if [ ! -d "$INSTALL_DIR" ]; then
    echo "Cloning the repository..."
    git clone "$REPO_URL" "$INSTALL_DIR"
    if [ $? -ne 0 ]; then
        echo "Error: Failed to clone the repository."
        exit 1
    fi
else
    echo "Repository already exists at $INSTALL_DIR. Pulling the latest changes..."
    cd "$INSTALL_DIR" || exit
    git pull
fi

cd "$INSTALL_DIR" || exit

# Check for Python installation
if ! command -v python3 &> /dev/null
then
    echo "Error: Python3 could not be found. Please install Python3 to proceed."
    exit 1
fi

# Check for pip installation
if ! command -v pip3 &> /dev/null
then
    echo "Error: pip3 could not be found. Please install pip3 to proceed."
    exit 1
fi

# Install required Python packages
echo "Installing required Python packages..."
if pip3 install -r requirements.txt; then
    echo "Python packages installed successfully."
else
    echo "Error: Failed to install Python packages."
    exit 1
fi

# Make the Python script executable and add shebang if not already present
if [ -f victron_monitor.py ]; then
    if ! head -n 1 victron_monitor.py | grep -q '^#!/usr/bin/env python3'; then
        echo "Adding shebang to victron_monitor.py..."
        sed -i '1s|^|#!/usr/bin/env python3\n|' victron_monitor.py
    fi
    echo "Making victron_monitor.py executable..."
    chmod +x victron_monitor.py
    sudo mv victron_monitor.py /usr/local/bin/victron_monitor
else
    echo "Error: victron_monitor.py not found!"
    exit 1
fi

# Create default settings.ini file if it doesn't exist
if [ ! -f settings.ini ]; then
    echo "Creating default settings.ini..."
    cat <<EOL > settings.ini
[DEFAULT]
TELEGRAM_TOKEN =
CHAT_ID =
VICTRON_API_URL =
API_KEY =
REFRESH_PERIOD = 5
MAX_POWER =
PASSTHRU_CURRENT =
NOMINAL_VOLTAGE = 230
TIMEZONE = UTC
EOL
    echo "Default settings.ini created."
else
    echo "settings.ini already exists, skipping creation."
fi

echo "Installation complete."
echo "Please run the \`victron_monitor\` command and choose '1. Configuration' to complete the initial setup."