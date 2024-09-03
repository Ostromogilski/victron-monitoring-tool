import configparser
import os
import sys
import requests
import asyncio
from telegram import Bot
from telegram.error import InvalidToken
from datetime import datetime
import pytz
import subprocess
import logging
from logging.handlers import RotatingFileHandler

# Set up logging with rotation
log_file = os.path.join(CONFIG_DIR, 'victron_monitor.log')
log_handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=5)
log_formatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s')
log_handler.setFormatter(log_formatter)

logging.basicConfig(level=logging.INFO, handlers=[log_handler], format='%(asctime)s %(levelname)s: %(message)s')

# Configuration
CONFIG_DIR = os.path.expanduser('~/victron_monitor/')
CONFIG_FILE = os.path.join(CONFIG_DIR, 'settings.ini')
GRID_ALARM_ID = 559
VE_BUS_ERROR_ID = 41
LOW_BATTERY_ID = 43
VOLTAGE_PHASE_1_ID = 8
VOLTAGE_PHASE_2_ID = 9
VOLTAGE_PHASE_3_ID = 10
OUTPUT_VOLTAGE_PHASE_1_ID = 20
OUTPUT_VOLTAGE_PHASE_2_ID = 21
OUTPUT_VOLTAGE_PHASE_3_ID = 22
OUTPUT_CURRENT_PHASE_1_ID = 23
OUTPUT_CURRENT_PHASE_2_ID = 24
OUTPUT_CURRENT_PHASE_3_ID = 25
VE_BUS_STATE_ID = 40
PASSTHRU_STATE = 8

# Default settings
DEFAULT_SETTINGS = {
    'TELEGRAM_TOKEN': '',
    'CHAT_ID': '',
    'VICTRON_API_URL': '',
    'API_KEY': '',
    'REFRESH_PERIOD': '5',  # Default refresh period set to 5 seconds
    'GRID_DOWN_MSG': '⚠️ Grid is down!',
    'GRID_UP_MSG': '✅ Grid is restored!',
    'LOW_BATTERY_MSG': '🪫 Low battery level!',
    'CRITICAL_BATTERY_MSG': '‼️🪫 Critical battery level!',
    'VE_BUS_ERROR_MSG': '🚨 Error: {error}.',
    'VE_BUS_RECOVERY_MSG': '🔧 System recovered from error.',
    'VOLTAGE_LOW_MSG': '📉 Input voltage on phase {phase} is too low: {voltage}V.',
    'VOLTAGE_HIGH_MSG': '📈 Input voltage on phase {phase} is too high: {voltage}V.',
    'VOLTAGE_NORMAL_MSG': '🆗 Input voltage on phase {phase} is within normal range: {voltage}V.',
    'CRITICAL_LOAD_MSG': '‼️ Critical load on phase {phase}: {power:.2f}W. Reduce consumption.',
    'PASSTHRU_MSG': '‼️ Critical load on phase {phase}: {power:.2f}W. Reduce consumption.',
    'MAX_POWER': '',
    'PASSTHRU_CURRENT': '',
    'NOMINAL_VOLTAGE': '230',
    'QUIET_HOURS_START': '',
    'QUIET_HOURS_END': '',
    'TIMEZONE': 'UTC'
}

# Function to create a default configuration file if it doesn't exist
def create_default_config():
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        
        config = configparser.ConfigParser()
        config['DEFAULT'] = DEFAULT_SETTINGS
        
        with open(CONFIG_FILE, 'w') as configfile:
            config.write(configfile)
    except PermissionError as e:
        logging.error(f"Cannot write configuration file: {e}")
        sys.exit(1)
    except FileNotFoundError as e:
        logging.error(f"Cannot create the directory or file: {e}")
        sys.exit(1)

# Function to load the configuration file
def load_config():
    if not os.path.exists(CONFIG_FILE):
        create_default_config()
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    return config

# Function to save the configuration file
def save_config(config):
    with open(CONFIG_FILE, 'w') as configfile:
        config.write(configfile)

# Function to check if essential configuration values are set
def validate_config(config):
    required_keys = [
        'TELEGRAM_TOKEN', 
        'CHAT_ID', 
        'VICTRON_API_URL', 
        'API_KEY', 
        'MAX_POWER', 
        'PASSTHRU_CURRENT'
    ]
    missing_keys = [key for key in required_keys if not config['DEFAULT'][key]]
    if missing_keys:
        print(f"Missing required configuration values: {', '.join(missing_keys)}")
        return False
    return True

# Function to list current settings
def list_settings(config):
    print("Current settings:")
    for key, value in config['DEFAULT'].items():
        print(f"{key}: {value}")

# Function to setup configuration
def setup_config():
    config = load_config()
    config['DEFAULT']['TELEGRAM_TOKEN'] = input("Enter Telegram bot token: ")
    config['DEFAULT']['CHAT_ID'] = input("Enter chat ID: ")
    installation_id = input("Enter your installation ID: ")
    config['DEFAULT']['INSTALLATION_ID'] = installation_id
    config['DEFAULT']['VICTRON_API_URL'] = f"https://vrmapi.victronenergy.com/v2/installations/{installation_id}/diagnostics"
    config['DEFAULT']['API_KEY'] = input("Enter Victron API token: ")
    config['DEFAULT']['REFRESH_PERIOD'] = input("Enter refresh period in seconds: ") or config['DEFAULT']['REFRESH_PERIOD']
    config['DEFAULT']['MAX_POWER'] = input("Enter max output power supported by your device in WATTS (W) (e.g., 4000): ") or config['DEFAULT']['MAX_POWER']
    config['DEFAULT']['PASSTHRU_CURRENT'] = input("Enter max output current supported by your device in AMPS (A) in passthru mode (e.g., 50): ") or config['DEFAULT']['PASSTHRU_CURRENT']
    config['DEFAULT']['NOMINAL_VOLTAGE'] = input("Enter nominal voltage in VOLTS (V) (e.g., 230): ") or config['DEFAULT']['NOMINAL_VOLTAGE']
    config['DEFAULT']['TIMEZONE'] = input("Enter timezone (e.g., Europe/Kyiv or UTC will be used): ") or config['DEFAULT']['TIMEZONE']
    save_config(config)
    print("Configuration saved successfully.")

# Function to setup language
def setup_language():
    config = load_config()

    # Language selection
    print("Select your preferred language:")
    print("1. English (default)")
    print("2. Українська (Ukrainian)")
    language_choice = input("Enter the number for your choice (1-2): ").strip()

    if language_choice == '2':
        config['DEFAULT']['LANGUAGE'] = 'uk'
    else:
        config['DEFAULT']['LANGUAGE'] = 'en'

    save_config(config)
    print("Language preference saved successfully.")

# Function to load messages based on language
def load_messages(config):
    if config['DEFAULT']['LANGUAGE'] == 'uk':
        messages = {
            'GRID_DOWN_MSG': '⚠️ Мережа відсутня!\n{timestamp}',
            'GRID_UP_MSG': '✅ Мережа відновлена!\n{timestamp}',
            'VE_BUS_ERROR_MSG': '🚨 Помилка:\n{ve_bus_status[1]}.\n{timestamp}',
            'VE_BUS_RECOVERY_MSG': '🔧 Система відновлена після помилки.\n{timestamp}',
            'LOW_BATTERY_MSG': '🪫 Низький заряд батареї!\n{timestamp}',
            'CRITICAL_BATTERY_MSG': '‼️🪫 Критичний заряд батареї!\n{timestamp}',
            'VOLTAGE_LOW_MSG': '📉 Вхідна напруга на {phase}-й фазі занадто низька: {voltage[0]}V.\n{timestamp}',
            'VOLTAGE_HIGH_MSG': '📈 Вхідна напруга на {phase}-й фазі занадто висока: {voltage[0]}V.\n{timestamp}',
            'VOLTAGE_NORMAL_MSG': '🆗 Вхідна напруга на {phase}-й фазі в межах норми: {voltage[0]}V.\n{timestamp}',
            'CRITICAL_LOAD_MSG': '‼️ Критичне навантаження на {phase}-й фазі: {power:.2f}W.\nЗменшіть споживання.\n{timestamp}',
            'PASSTHRU_MSG': '‼️ Критичне навантаження на {phase}-й фазі: {power:.2f}W.\nЗменшіть споживання.\n{timestamp}'
        }
    else:
        messages = {
            'GRID_DOWN_MSG': '⚠️ Grid is down!\n{timestamp}',
            'GRID_UP_MSG': '✅ Grid is restored!\n{timestamp}',
            'VE_BUS_ERROR_MSG': '🚨 Error: {error}.\n{timestamp}',
            'VE_BUS_RECOVERY_MSG': '🔧 System recovered from error.\n{timestamp}',
            'LOW_BATTERY_MSG': '🪫 Low battery level!\n{timestamp}',
            'CRITICAL_BATTERY_MSG': '‼️🪫 Critical battery level!\n{timestamp}',
            'VOLTAGE_LOW_MSG': '📉 Input voltage on phase {phase} is too low: {voltage[0]}V.\n{timestamp}',
            'VOLTAGE_HIGH_MSG': '📈 Input voltage on phase {phase} is too high: {voltage[0]}V.\n{timestamp}',
            'VOLTAGE_NORMAL_MSG': '🆗 Input voltage on phase {phase} is within normal range: {voltage[0]}V.\n{timestamp}',
            'CRITICAL_LOAD_MSG': '‼️ Critical load on phase {phase}: {power:.2f}W.\nReduce consumption.\n{timestamp}',
            'PASSTHRU_MSG': '‼️ Critical load on phase {phase}: {power:.2f}W.\nReduce consumption.\n{timestamp}'
        }

    return messages

# Function to enable service at startup (placeholder for actual implementation)
SERVICE_NAME = 'victron_monitor.service'
SERVICE_FILE = f'/etc/systemd/system/{SERVICE_NAME}'

def is_service_enabled():
    return os.path.isfile(SERVICE_FILE)

def enable_startup():
    config = load_config()
    if not validate_config(config):
        print("Cannot enable service. Please complete the configuration first.")
        return

    if is_service_enabled():
        print("Service is currently enabled. Do you want to disable it? (y/n)")
        choice = input().strip().lower()
        if choice == 'y':
            disable_startup()
        else:
            print("Service remains enabled.")
    else:
        print("Service is currently disabled. Do you want to enable it? (y/n)")
        choice = input().strip().lower()
        if choice == 'y':
            if create_service_file():
                try:
                    subprocess.run(['sudo', 'systemctl', 'enable', SERVICE_NAME], check=True)
                    subprocess.run(['sudo', 'systemctl', 'start', SERVICE_NAME], check=True)
                    print("Service enabled at startup.")
                except subprocess.CalledProcessError as e:
                    print(f"Failed to enable/start service: {e}")
            else:
                print("Failed to create service file. Ensure you have the necessary permissions.")
        else:
            print("Service remains disabled.")

def create_service_file():
    script_path = os.path.abspath(__file__)

    service_file_content = f"""
[Unit]
Description=Victron Monitoring Tool
After=network.target

[Service]
ExecStart={script_path}
Restart=always
User={os.getlogin()}
Group={os.getlogin()}

[Install]
WantedBy=multi-user.target
"""

    try:
        command = f'echo "{service_file_content}" | sudo tee {SERVICE_FILE} > /dev/null'
        subprocess.run(command, shell=True, check=True)

        subprocess.run(['sudo', 'systemctl', 'daemon-reload'], check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error creating the service file: {e}")
        return False

def disable_startup():
    if is_service_enabled():
        try:
            subprocess.run(['sudo', 'systemctl', 'disable', SERVICE_NAME], check=True)
            subprocess.run(['sudo', 'systemctl', 'stop', SERVICE_NAME], check=True)
            
            subprocess.run(['sudo', 'rm', SERVICE_FILE], check=True)
            
            subprocess.run(['sudo', 'systemctl', 'daemon-reload'], check=True)
            print("Service disabled at startup.")
        except subprocess.CalledProcessError as e:
            print(f"Failed to disable/start service: {e}")
    else:
        print("Service is already disabled.")

def check_status():
    if is_service_enabled():
        status = subprocess.run(['systemctl', 'is-active', SERVICE_NAME], capture_output=True, text=True)
        if status.stdout.strip() == "active":
            print("Service is currently running.")
        else:
            print("Service is enabled but not running.")
    else:
        print("Service is not enabled.")

def view_logs():
    if is_service_enabled():
        print("Fetching logs for the Victron Monitoring Tool...")
        subprocess.run(['journalctl', '-u', SERVICE_NAME, '--no-pager', '--lines=100'])
    else:
        print("Service is not enabled. No logs to display.")

def setup_quiet_hours():
    config = load_config()
    print("Set Quiet Hours (24h format, hour increments):")
    start = input("Enter start of quiet hours (e.g., 22) or leave blank to disable: ").strip()
    end = input("Enter end of quiet hours (e.g., 8) or leave blank to disable: ").strip()

    if start.isdigit() and end.isdigit():
        start, end = int(start), int(end)
        if 0 <= start <= 23 and 0 <= end <= 23:
            config['DEFAULT']['QUIET_HOURS_START'] = str(start)
            config['DEFAULT']['QUIET_HOURS_END'] = str(end)
            print(f"Quiet Hours set from {start}:00 to {end}:00")
        else:
            print("Invalid input for hours. Please enter values between 0 and 23.")
    else:
        config['DEFAULT']['QUIET_HOURS_START'] = ''
        config['DEFAULT']['QUIET_HOURS_END'] = ''
        print("Quiet Hours Disabled")

    save_config(config)
    print("Quiet Hours configuration saved successfully.")

# Function to handle CLI arguments and menu
def main():
    if not sys.stdin.isatty():
        print("Running in non-interactive mode. Skipping the menu.")
        return

    while True:
        config = load_config()
        quiet_hours_status = f"{config['DEFAULT']['QUIET_HOURS_START']}:00 to {config['DEFAULT']['QUIET_HOURS_END']}:00" \
            if config['DEFAULT']['QUIET_HOURS_START'] and config['DEFAULT']['QUIET_HOURS_END'] else "Disabled"
        
        service_status = "(Enabled)" if is_service_enabled() else "(Disabled)"
        
        print("Please choose an option:")
        print(f"1. Configuration")
        print(f"2. Enable or disable service at startup {service_status}")
        print(f"3. Language menu")
        print(f"4. Set Quiet Hours ({quiet_hours_status})")
        print("5. Check Status")
        print("6. View Logs")
        print("7. Exit")
        
        choice = input("Enter your choice (1-7): ")

        if choice == '1':
            setup_config()
        elif choice == '2':
            config = load_config()
            if validate_config(config):
                enable_startup()
            else:
                print("Cannot enable service. Please complete the configuration first.")
        elif choice == '3':
            setup_language()
        elif choice == '4':
            setup_quiet_hours()
        elif choice == '5':
            check_status()
        elif choice == '6':
            view_logs()
        elif choice == '7':
            sys.exit(0)
        else:
            print("Invalid choice. Please try again.")

if __name__ == '__main__':
    main()
    
# Function to get the status of grid, VE.Bus error, low battery, and input/output voltages and currents
def get_status(VICTRON_API_URL, API_KEY):
    headers = {
        'x-authorization': f'Token {API_KEY}',
        'Content-Type': 'application/json'
    }
    try:
        response = requests.get(VICTRON_API_URL, headers=headers)
        response.raise_for_status()
    except requests.exceptions.HTTPError as http_err:
        logging.error(f"HTTP error occurred: {http_err}")
        return None, None, None, None, None, None, None
    except requests.exceptions.ConnectionError as conn_err:
        logging.error(f"Error connecting: {conn_err}")
        return None, None, None, None, None, None, None
    except requests.exceptions.Timeout as timeout_err:
        logging.error(f"Timeout error: {timeout_err}")
        return None, None, None, None, None, None, None
    except requests.exceptions.RequestException as req_err:
        logging.error(f"Request error: {req_err}")
        return None, None, None, None, None, None, None

    try:
        diagnostics = response.json()
        grid_status, ve_bus_status, low_battery_status = None, None, None
        voltage_phases = {1: None, 2: None, 3: None}
        output_voltages = {1: None, 2: None, 3: None}
        output_currents = {1: None, 2: None, 3: None}
        ve_bus_state = None

        if 'records' in diagnostics:
            for diagnostic in diagnostics['records']:
                if diagnostic['idDataAttribute'] == GRID_ALARM_ID:
                    grid_status = diagnostic['rawValue'], diagnostic['formattedValue']
                elif diagnostic['idDataAttribute'] == VE_BUS_ERROR_ID:
                    ve_bus_status = diagnostic['rawValue'], diagnostic['formattedValue']
                elif diagnostic['idDataAttribute'] == VE_BUS_STATE_ID:
                    ve_bus_state = diagnostic['rawValue']
                elif diagnostic['idDataAttribute'] == LOW_BATTERY_ID:
                    low_battery_status = diagnostic['rawValue'], diagnostic['formattedValue']
                elif diagnostic['idDataAttribute'] == VOLTAGE_PHASE_1_ID:
                    voltage_phases[1] = float(diagnostic['rawValue']), diagnostic['formattedValue']
                elif diagnostic['idDataAttribute'] == VOLTAGE_PHASE_2_ID:
                    voltage_phases[2] = float(diagnostic['rawValue']), diagnostic['formattedValue']
                elif diagnostic['idDataAttribute'] == VOLTAGE_PHASE_3_ID:
                    voltage_phases[3] = float(diagnostic['rawValue']), diagnostic['formattedValue']
                elif diagnostic['idDataAttribute'] == OUTPUT_VOLTAGE_PHASE_1_ID:
                    output_voltages[1] = float(diagnostic['rawValue']), diagnostic['formattedValue']
                elif diagnostic['idDataAttribute'] == OUTPUT_VOLTAGE_PHASE_2_ID:
                    output_voltages[2] = float(diagnostic['rawValue']), diagnostic['formattedValue']
                elif diagnostic['idDataAttribute'] == OUTPUT_VOLTAGE_PHASE_3_ID:
                    output_voltages[3] = float(diagnostic['rawValue']), diagnostic['formattedValue']
                elif diagnostic['idDataAttribute'] == OUTPUT_CURRENT_PHASE_1_ID:
                    output_currents[1] = float(diagnostic['rawValue']), diagnostic['formattedValue']
                elif diagnostic['idDataAttribute'] == OUTPUT_CURRENT_PHASE_2_ID:
                    output_currents[2] = float(diagnostic['rawValue']), diagnostic['formattedValue']
                elif diagnostic['idDataAttribute'] == OUTPUT_CURRENT_PHASE_3_ID:
                    output_currents[3] = float(diagnostic['rawValue']), diagnostic['formattedValue']

        return grid_status, ve_bus_status, low_battery_status, voltage_phases, output_voltages, output_currents, ve_bus_state
    except ValueError as e:
        print("Error parsing JSON:", e)
        return None, None, None, None, None, None, None

# Async function to send a message to the Telegram group
async def send_telegram_message(bot, CHAT_ID, message, TIMEZONE):
    local_tz = pytz.timezone(TIMEZONE)
    current_hour = datetime.now(local_tz).hour
    config = load_config()
    quiet_hours_start = config['DEFAULT'].getint('QUIET_HOURS_START', fallback=None)
    quiet_hours_end = config['DEFAULT'].getint('QUIET_HOURS_END', fallback=None)

    # Determine if the message should be sent silently
    disable_notification = False
    if quiet_hours_start is not None and quiet_hours_end is not None:
        if quiet_hours_start < quiet_hours_end:
            disable_notification = quiet_hours_start <= current_hour < quiet_hours_end
        else:  # Handles the case where quiet hours span midnight
            disable_notification = current_hour >= quiet_hours_start or current_hour < quiet_hours_end

    await bot.send_message(chat_id=CHAT_ID, text=message, disable_notification=disable_notification)

# Monitor loop
async def monitor():
    last_grid_status = None
    last_ve_bus_status = None
    last_low_battery_status = None
    voltage_issue_reported = {1: False, 2: False, 3: False}
    last_voltage_phases = {1: None, 2: None, 3: None}
    power_issue_counters = {1: 0, 2: 0, 3: 0}
    power_issue_reported = {1: False, 2: False, 3: False}
    first_run = True  # Flag to indicate the first run
    config = load_config()
    settings = config['DEFAULT']
    messages = load_messages(config)

    TELEGRAM_TOKEN = settings['TELEGRAM_TOKEN']
    CHAT_ID = settings['CHAT_ID']
    VICTRON_API_URL = settings['VICTRON_API_URL']
    API_KEY = settings['API_KEY']
    REFRESH_PERIOD = int(settings['REFRESH_PERIOD'])
    TIMEZONE = settings['TIMEZONE']
    
    # Check if essential configuration values are set
    if not TELEGRAM_TOKEN or not CHAT_ID or not VICTRON_API_URL or not API_KEY:
        logging.error("Essential configuration values are missing. Please set them in the configuration.")
        return  # Exit the monitoring function without running it

    try:
        bot = Bot(token=TELEGRAM_TOKEN)
    except telegram.error.InvalidToken:
        logging.error("Invalid Telegram token provided. Please check your configuration.")
        return

    local_tz = pytz.timezone(TIMEZONE)

    while True:
        try:
            # Fetch the current status from the Victron API
            grid_status, ve_bus_status, low_battery_status, voltage_phases, output_voltages, output_currents, ve_bus_state = get_status(VICTRON_API_URL, API_KEY)
            timestamp = datetime.now(local_tz).strftime("%d.%m.%Y %H:%M")

            # Skip sending messages on the first run to set the initial states
            if first_run:
                last_grid_status = grid_status
                last_ve_bus_status = ve_bus_status
                last_low_battery_status = low_battery_status
                last_voltage_phases = voltage_phases
                first_run = False
                await asyncio.sleep(REFRESH_PERIOD)
                continue

            # Check and send grid status updates independently
            if grid_status is not None and grid_status != last_grid_status:
                if grid_status[0] == 2:
                    message = messages['GRID_DOWN_MSG'].replace('{timestamp}', timestamp)
                    await send_telegram_message(bot, CHAT_ID, message, TIMEZONE)
                elif grid_status[0] == 0:
                    message = messages['GRID_UP_MSG'].replace('{timestamp}', timestamp)
                    await send_telegram_message(bot, CHAT_ID, message, TIMEZONE)
                last_grid_status = grid_status

            # Check and send VE.Bus error updates independently
            if ve_bus_status is not None and ve_bus_status != last_ve_bus_status:
                if ve_bus_status[1] == "No error":
                    message = messages['VE_BUS_RECOVERY_MSG'].replace('{timestamp}', timestamp)
                    await send_telegram_message(bot, CHAT_ID, message, TIMEZONE)
                else:
                    message = messages['VE_BUS_ERROR_MSG'].replace('{error}', ve_bus_status[1]).replace('{timestamp}', timestamp)
                    await send_telegram_message(bot, CHAT_ID, message, TIMEZONE)
                last_ve_bus_status = ve_bus_status

            # Check and send low battery status updates independently
            if low_battery_status is not None and low_battery_status != last_low_battery_status:
                if last_low_battery_status is None or last_low_battery_status[0] == 0:
                    if low_battery_status[0] == 1:
                        message = messages['LOW_BATTERY_MSG'].replace('{timestamp}', timestamp)
                        await send_telegram_message(bot, CHAT_ID, message, TIMEZONE)
                    elif low_battery_status[0] == 2:
                        message = messages['CRITICAL_BATTERY_MSG'].replace('{timestamp}', timestamp)
                        await send_telegram_message(bot, CHAT_ID, message, TIMEZONE)
                elif last_low_battery_status[0] == 1 and low_battery_status[0] == 2:
                    message = messages['CRITICAL_BATTERY_MSG'].replace('{timestamp}', timestamp)
                    await send_telegram_message(bot, CHAT_ID, message, TIMEZONE)
                
                last_low_battery_status = low_battery_status

            # Check and send voltage phase updates independently
            for phase in range(1, 4):
                voltage = voltage_phases[phase]
                last_voltage = last_voltage_phases[phase]

                # Load nominal voltage and calculate thresholds
                nominal_voltage = float(settings['NOMINAL_VOLTAGE'])
                voltage_low_threshold = nominal_voltage * 0.90  # 10% less than nominal voltage
                voltage_high_threshold = nominal_voltage * 1.10  # 10% more than nominal voltage
                voltage_normal_low = nominal_voltage * 0.957  # 4.3% less than nominal voltage
                voltage_normal_high = nominal_voltage * 1.043  # 4.3% more than nominal voltage

                if voltage is not None and voltage[0] > 0:  # Check if voltage is greater than 0
                    if voltage[0] < voltage_low_threshold and not voltage_issue_reported[phase]:
                        message = messages['VOLTAGE_LOW_MSG'].replace('{phase}', str(phase)).replace('{voltage}', str(voltage[0])).replace('{timestamp}', timestamp)
                        await send_telegram_message(bot, CHAT_ID, message, TIMEZONE)
                        voltage_issue_reported[phase] = True
                    elif voltage[0] > voltage_high_threshold and not voltage_issue_reported[phase]:
                        message = messages['VOLTAGE_HIGH_MSG'].replace('{phase}', str(phase)).replace('{voltage}', str(voltage[0])).replace('{timestamp}', timestamp)
                        await send_telegram_message(bot, CHAT_ID, message, TIMEZONE)
                        voltage_issue_reported[phase] = True
                    elif voltage_normal_low <= voltage[0] <= voltage_normal_high and voltage_issue_reported[phase]:
                        message = messages['VOLTAGE_NORMAL_MSG'].replace('{phase}', str(phase)).replace('{voltage}', str(voltage[0])).replace('{timestamp}', timestamp)
                        await send_telegram_message(bot, CHAT_ID, message, TIMEZONE)
                        voltage_issue_reported[phase] = False

                last_voltage_phases[phase] = voltage

            # Check power consumption on each phase if the grid is absent
            if grid_status and grid_status[0] == 2:
                for phase in range(1, 4):
                    if output_voltages[phase] is not None and output_currents[phase] is not None:
                        power = output_voltages[phase][0] * output_currents[phase][0]
                        max_power = float(settings['MAX_POWER'])
                        power_limit = max_power * 0.98  # 2% less than MAX_POWER
                        power_reset_threshold = max_power * 0.80  # 20% less than MAX_POWER
                        
                        if power > power_limit:
                            power_issue_counters[phase] += 1
                            if power_issue_counters[phase] >= 2 and not power_issue_reported[phase]:  # If power > power_limit for 2 refreshes
                                message = messages['CRITICAL_LOAD_MSG'].replace('{phase}', str(phase)).replace('{power}', f"{power:.2f}").replace('{timestamp}', timestamp)
                                await send_telegram_message(bot, CHAT_ID, message, TIMEZONE)
                                power_issue_reported[phase] = True
                        elif power < power_reset_threshold:
                            power_issue_counters[phase] = 0  # Reset the counter if power drops below power_reset_threshold
                            power_issue_reported[phase] = False  # Reset the reported flag to allow future alerts
                    else:
                        power_issue_counters[phase] = 0  # Reset the counter if there's no valid data for the phase

             # Check power consumption on each phase if the VE.Bus state is "Passthru"
            if ve_bus_state == PASSTHRU_STATE:
                for phase in range(1, 4):
                    if output_voltages[phase] is not None and output_currents[phase] is not None:
                        # Calculate power by multiplying the voltage and current for the phase
                        power = output_voltages[phase][0] * output_currents[phase][0]
                        passthru_current_limit = float(settings['PASSTHRU_CURRENT']) * 0.98  # 2% less than PASSTHRU_CURRENT
                        passthru_current_reset_threshold = float(settings['PASSTHRU_CURRENT']) * 0.85  # 15% less than PASSTHRU_CURRENT
                        
                        if power > passthru_current_limit:
                            power_issue_counters[phase] += 1
                            if power_issue_counters[phase] >= 2 and not power_issue_reported[phase]:
                                message = messages['PASSTHRU_MSG'].replace('{phase}', str(phase)).replace('{power}', f"{power:.2f}").replace('{timestamp}', timestamp)
                                await send_telegram_message(bot, CHAT_ID, message, TIMEZONE)
                                power_issue_reported[phase] = True
                        elif power < passthru_current_reset_threshold:
                            power_issue_counters[phase] = 0  # Reset the counter if power drops below the reset threshold
                            power_issue_reported[phase] = False  # Reset the reported flag to allow future alerts
                    else:
                        power_issue_counters[phase] = 0  # Reset the counter if there's no valid data for the phase


            await asyncio.sleep(REFRESH_PERIOD)
        except Exception as e:
            logging.error(f"Error: {e}")
            await asyncio.sleep(REFRESH_PERIOD)

# Run the monitoring loop
if __name__ == '__main__':
    asyncio.run(monitor())