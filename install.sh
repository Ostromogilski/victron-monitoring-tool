#!/bin/bash

# Check if the script is being run with sudo
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (e.g., sudo bash <script>)"
  exit 1
fi

# Get the original user's home directory, explicitly using SUDO_USER
if [ -n "$SUDO_USER" ]; then
    USER_HOME=$(eval echo "~$SUDO_USER")
else
    USER_HOME=$HOME
fi

# Define repository URL and installation directory
REPO_URL="https://github.com/Ostromogilski/victron-monitoring-tool.git"
INSTALL_DIR="/opt/victron-monitoring-tool"
CONFIG_DIR="$USER_HOME/victron_monitor"
CONFIG_FILE="$CONFIG_DIR/settings.ini"
SERVICE_NAME="victron_monitor.service"
SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME"
BIN_FILE="/usr/local/bin/victron_monitor"

# Default settings for settings.ini
DEFAULT_SETTINGS=$(cat <<EOF
[DEFAULT]
TELEGRAM_TOKEN=
CHAT_ID=
VICTRON_API_URL=
API_KEY=
REFRESH_PERIOD=5
MAX_POWER=
PASSTHRU_CURRENT=
NOMINAL_VOLTAGE=230
QUIET_HOURS_START=
QUIET_HOURS_END=
TIMEZONE=UTC
LANGUAGE=en
INSTALLATION_ID=
EOF
)

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

            # Backup existing settings.ini
            if [ -f "$CONFIG_FILE" ]; then
                echo "Backing up current settings.ini..."
                cp "$CONFIG_FILE" "$CONFIG_FILE.bak"
            fi

            # Ensure all files from the repository are up to date
            echo "Ensuring all files from the repository are up to date..."
            cd "$INSTALL_DIR" || exit

            # Attempt to reset local changes and capture errors
            git reset --hard 2>&1 | tee git_output.log
            if grep -q "fatal" git_output.log; then
                echo "A fatal error occurred. Re-cloning the repository..."
                rm -rf "$INSTALL_DIR"

                # Re-clone the repository
                git clone "$REPO_URL" "$INSTALL_DIR"
                if [ $? -ne 0 ]; then
                    echo "Error: Failed to clone the repository."
                    exit 1
                fi
            else
                # Pull the latest changes from the repository
                git pull
                if [ $? -ne 0 ]; then
                    echo "Error: Failed to update the repository."
                    exit 1
                fi
            fi

            # Restore settings.ini after re-cloning if it was backed up
            if [ -f "$CONFIG_FILE.bak" ]; then
                echo "Restoring settings.ini..."
                mv "$CONFIG_FILE.bak" "$CONFIG_FILE"
            fi

            # Ensure victron_monitor.py has a Python shebang
            if [ -f "$INSTALL_DIR/victron_monitor.py" ]; then
                if ! head -n 1 "$INSTALL_DIR/victron_monitor.py" | grep -q '^#!/usr/bin/env python3'; then
                    echo "Adding Python shebang to victron_monitor.py..."
                    sed -i '1s|^|#!/usr/bin/env python3\n|' "$INSTALL_DIR/victron_monitor.py"
                fi
            else
                echo "Error: victron_monitor.py not found!"
                exit 1
            fi

            # Ensure the target script is executable
            echo "Making victron_monitor.py executable..."
            sudo chmod +x "$INSTALL_DIR/victron_monitor.py"

            # Reinitialize /usr/local/bin/victron_monitor as a symlink to the script
            echo "Reinitializing victron_monitor in /usr/local/bin..."
            sudo ln -sf "$INSTALL_DIR/victron_monitor.py" /usr/local/bin/victron_monitor
            if [ $? -ne 0 ]; then
                echo "Error: Failed to create the symlink for victron_monitor."
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

            if [ -f "$SERVICE_FILE" ]; then
                echo "Disabling the service..."
                systemctl disable "$SERVICE_NAME"
                echo "Removing service file..."
                rm -f "$SERVICE_FILE"
                systemctl daemon-reload
            else
                echo "Service file does not exist, skipping."
            fi

            # Remove installation directory and its contents
            if [ -d "$INSTALL_DIR" ]; then
                echo "Removing application files..."
                rm -rf "$INSTALL_DIR"
            else
                echo "$INSTALL_DIR does not exist, skipping."
            fi

            # Remove the binary or symlink from /usr/local/bin
            if [ -f "$BIN_FILE" ]; then
                echo "Removing victron_monitor from /usr/local/bin..."
                rm -f "$BIN_FILE"
                if [ ! -f "$BIN_FILE" ]; then
                    echo "victron_monitor successfully removed from /usr/local/bin."
                else
                    echo "Failed to remove victron_monitor from /usr/local/bin."
                fi
            else
                echo "victron_monitor does not exist in /usr/local/bin, skipping."
            fi

            # Remove the settings directory created by the Python script
            if [ -d "$CONFIG_DIR" ]; then
                echo "Removing configuration directory $CONFIG_DIR..."
                rm -rf "$CONFIG_DIR"
            else
                echo "$CONFIG_DIR does not exist, skipping."
            fi

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