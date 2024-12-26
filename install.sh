#!/bin/bash

# ------------------------------------------------------------------------------
# 1. Must be run as root
# ------------------------------------------------------------------------------
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (e.g., sudo bash <script>)"
  exit 1
fi

# ------------------------------------------------------------------------------
# 2. Figure out who the "real" user is (in case of sudo)
# ------------------------------------------------------------------------------
if [ -n "$SUDO_USER" ]; then
    USER_HOME=$(eval echo "~$SUDO_USER")
else
    USER_HOME=$HOME
fi

# ------------------------------------------------------------------------------
# 3. Locate a Python interpreter that is >= 3.13.1
# ------------------------------------------------------------------------------
PYTHON_EXE=""
if command -v python3 &>/dev/null; then
    PYTHON_EXE="$(command -v python3)"
else
    echo "Error: No python3 found on the system."
    exit 1
fi

PY_VERSION=$("$PYTHON_EXE" --version 2>&1 | awk '{print $2}')
IFS='.' read -r major minor patch <<< "$PY_VERSION"
patch=${patch:-0}   # if patch part is empty, treat as 0

if [ "$major" -lt 3 ] ||
   ( [ "$major" -eq 3 ] && [ "$minor" -lt 13 ] ) ||
   ( [ "$major" -eq 3 ] && [ "$minor" -eq 13 ] && [ "$patch" -lt 1 ] ); then
    echo "Error: Found Python $PY_VERSION, but we need 3.13.1 or higher."
    exit 1
fi

echo "Detected Python $PY_VERSION at $PYTHON_EXE which meets the requirement (>= 3.13.1)."

# ------------------------------------------------------------------------------
# 4. Ensure pip is available for our chosen Python (3.13.1+)
# ------------------------------------------------------------------------------
if ! "$PYTHON_EXE" -m pip --version &>/dev/null; then
    echo "Pip not found for Python $PY_VERSION, attempting to install via ensurepip..."
    if ! "$PYTHON_EXE" -m ensurepip --upgrade; then
        echo "Error: Failed to install pip for $PYTHON_EXE."
        exit 1
    fi
fi

# ------------------------------------------------------------------------------
# 5. Function to install Python packages with the chosen Python interpreter
# ------------------------------------------------------------------------------
install_python_packages() {
    echo "Installing required Python packages with $PYTHON_EXE..."
    
    # 5.1. Upgrade pip, setuptools, wheel first (good practice)
    if ! "$PYTHON_EXE" -m pip install --upgrade pip setuptools wheel; then
        echo "Error: Failed to upgrade pip, setuptools, and wheel."
        exit 1
    fi
    
    # 5.2. Then install requirements
    if "$PYTHON_EXE" -m pip install -r requirements.txt; then
        echo "Python packages installed successfully."
    else
        echo "Error: Failed to install Python packages."
        exit 1
    fi
}

# ------------------------------------------------------------------------------
# 6. Define constants for your project
# ------------------------------------------------------------------------------
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
QUIET_DAYS=
TIMEZONE=UTC
LANGUAGE=en
INSTALLATION_ID=
LOG_LEVEL=INFO
EOF
)

# Check for git installation
if ! command -v git &> /dev/null; then
    echo "Error: git could not be found. Please install git to proceed."
    exit 1
fi

# ------------------------------------------------------------------------------
# 7. Check if the application is already installed
# ------------------------------------------------------------------------------
if [ -d "$INSTALL_DIR" ] || [ -f "$SERVICE_FILE" ] || [ -f "$BIN_FILE" ]; then
    echo "Victron Monitoring Tool is already installed."
    echo "Please select an option:"
    echo "1. Update (keeps settings.ini and restarts the service)"
    echo "2. Uninstall (removes all files and disables the service)"
    echo "3. Cancel"
    read -r -p "Enter your choice [1-3]: " choice

    case $choice in
        1)
            echo "Updating the application..."

            echo "Ensuring all files from the repository are up to date..."
            cd "$INSTALL_DIR" || exit

            # Reset local changes and pull
            git reset --hard
            if [ $? -ne 0 ]; then
                echo "Error: Failed to reset the repository."
                exit 1
            fi
            git pull
            if [ $? -ne 0 ]; then
                echo "Error: Failed to update the repository."
                exit 1
            fi

            # Ensure victron_monitor.py exists and has correct shebang
            if [ ! -f "$INSTALL_DIR/victron_monitor.py" ]; then
                echo "Error: victron_monitor.py not found!"
                exit 1
            fi
            # Overwrite any existing shebang with the correct Python
            sed -i "1s|^#!.*|#!${PYTHON_EXE}|" "$INSTALL_DIR/victron_monitor.py"
            
            echo "Making victron_monitor.py executable..."
            chmod +x "$INSTALL_DIR/victron_monitor.py"

            echo "Reinitializing victron_monitor in /usr/local/bin..."
            ln -sf "$INSTALL_DIR/victron_monitor.py" "$BIN_FILE"
            if [ $? -ne 0 ]; then
                echo "Error: Failed to create the symlink for victron_monitor."
                exit 1
            fi

            # Install/upgrade Python packages
            cd "$INSTALL_DIR" || exit
            install_python_packages

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

            if [ -d "$INSTALL_DIR" ]; then
                echo "Removing application files from $INSTALL_DIR..."
                rm -rf "$INSTALL_DIR"
                if [ $? -eq 0 ]; then
                    echo "$INSTALL_DIR successfully removed."
                else
                    echo "Error: Failed to remove $INSTALL_DIR."
                    exit 1
                fi
            else
                echo "$INSTALL_DIR does not exist, skipping."
            fi

            if [ -f "$BIN_FILE" ] || [ -L "$BIN_FILE" ]; then
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
    # ----------------------------------------------------------------------------
    # Fresh install
    # ----------------------------------------------------------------------------
    echo "Installing the Victron Monitoring Tool..."

    echo "Cloning the repository..."
    git clone "$REPO_URL" "$INSTALL_DIR"
    if [ $? -ne 0 ]; then
        echo "Error: Failed to clone the repository."
        exit 1
    fi

    cd "$INSTALL_DIR" || exit

    # Install requirements with the chosen Python
    install_python_packages

    # Put correct shebang in victron_monitor.py
    if [ ! -f victron_monitor.py ]; then
        echo "Error: victron_monitor.py not found!"
        exit 1
    fi
    # Overwrite any existing shebang with the correct interpreter
    sed -i "1s|^#!.*|#!${PYTHON_EXE}|" victron_monitor.py

    echo "Making victron_monitor.py executable..."
    chmod +x victron_monitor.py
    mv victron_monitor.py "$BIN_FILE"

    echo "Installation complete."
    echo "Please run the \`victron_monitor\` command and choose '1. Configuration' to complete the initial setup."
fi