#!/bin/bash

# Define repository URL and installation directory
REPO_URL="https://github.com/Ostromogilski/victron-monitoring-tool.git"
INSTALL_DIR="/opt/victron-monitoring-tool"
CONFIG_FILE="$INSTALL_DIR/settings.ini"
SERVICE_NAME="victron_monitor.service"
SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME"

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

# Check if the application already exists
if [ -d "$INSTALL_DIR" ]; then
    echo "Victron Monitoring Tool is already installed."
    echo "Please select an option:"
    echo "1. Update (keeps settings.ini and restarts the service)"
    echo "2. Uninstall (removes all files and disables the service)"
    echo "3. Cancel"
    read -r -p "Enter your choice [1-3]: " choice

    case $choice in
        1)
            echo "Updating the application..."

            # Pull the latest changes
            cd "$INSTALL_DIR" || exit
            git pull
            if [ $? -ne 0 ]; then
                echo "Error: Failed to update the repository."
                exit 1
            fi

            # Ensure settings.ini is not overwritten
            if [ ! -f "$CONFIG_FILE" ]; then
                echo "Error: settings.ini not found. Exiting update."
                exit 1
            fi
            echo "settings.ini preserved."

            # Install required Python packages
            echo "Installing required Python packages..."
            if pip3 install -r requirements.txt; then
                echo "Python packages installed successfully."
            else
                echo "Error: Failed to install Python packages."
                exit 1
            fi

            # Restart the service if it exists
            if systemctl is-active --quiet "$SERVICE_NAME"; then
                echo "Restarting the service..."
                systemctl restart "$SERVICE_NAME"
                if [ $? -eq 0 ]; then
                    echo "Service restarted successfully."
                else
                    echo "Error: Failed to restart the service."
                    exit 1
                fi
            else
                echo "Service is not active. Please start it manually if needed."
            fi

            echo "Update completed."
            ;;
        2)
            echo "Uninstalling the application..."

            # Stop and disable the service
            if systemctl is-active --quiet "$SERVICE_NAME"; then
                echo "Stopping the service..."
                systemctl stop "$SERVICE_NAME"
            fi
            echo "Disabling the service..."
            systemctl disable "$SERVICE_NAME"

            # Remove service file
            echo "Removing service file..."
            rm -f "$SERVICE_FILE"
            systemctl daemon-reload

            # Remove installation directory
            echo "Removing application files..."
            rm -rf "$INSTALL_DIR"

            echo "Uninstallation complete."
            ;;
        3)
            echo "Operation cancelled."
            exit 0
            ;;
        *)
            echo "Invalid option. Exiting."
            exit 1
            ;;
    esac
else
    echo "Installing the Victron Monitoring Tool..."

    # Clone the repository
    echo "Cloning the repository..."
    git clone "$REPO_URL" "$INSTALL_DIR"
    if [ $? -ne 0 ]; then
        echo "Error: Failed to clone the repository."
        exit 1
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

    echo "Installation complete."
    echo "Please run the \`victron_monitor\` command and choose '1. Configuration' to complete the initial setup."
fi