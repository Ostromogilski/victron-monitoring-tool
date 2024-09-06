# Victron Monitoring Tool

This is a monitoring tool for Victron Energy systems that sends alerts to a specified Telegram chat when certain conditions are met, such as grid down, low battery, or critical load. The tool is configurable and customizable to fit various use cases.

## Features

- Monitors grid status, battery levels, voltage, and power on each phase.
- Sends notifications to a Telegram chat.
- Customizable alert messages.
- Configurable refresh period, voltage thresholds, and power limits.

## Installation

### Prerequisites

- Python 3.x
- `pip` (Python package installer)

### Steps

1. **Download and Run the Install Script**

    Run the following command in your terminal to download and execute the installation script:

    ```bash
    sudo bash -c "$(curl -s https://raw.githubusercontent.com/Ostromogilski/victron-monitoring-tool/main/install.sh)"
    ```

    This script will install the necessary dependencies, make the Python script executable, and create a default `settings.ini` file if it doesn't already exist.

2. **Start the Application for Initial Configuration**

    After the installation, run the monitoring tool to enter the setup and configure your application:

    ```bash
    victron_monitor
    ```

3. **Complete the Initial Setup**

    1. Inside the application, choose `1. Configuration` from the menu to enter your Telegram bot token, chat ID, Victron API URL, API key, refresh period, max power, passthru current, nominal voltage, and timezone.

4. **Run the Monitoring Tool**

    Once configured, the monitoring tool will start monitoring your Victron system based on the settings provided:

    ```bash
    victron_monitor
    ```

## Usage

The tool continuously monitors your Victron system according to the configuration and sends alerts based on the conditions set in `settings.ini`.