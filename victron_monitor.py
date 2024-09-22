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
import readline
from tuya_connector import TuyaOpenAPI
import aioconsole

#Global variables
dev_mode = False
simulated_values = {}
reset_last_values = False

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
PASSTHRU_STATE = 9
GREEN_TEXT = '\033[92m'
RED_TEXT = '\033[91m'
RESET_TEXT = '\033[0m'

# Set up logging with rotation
os.makedirs(CONFIG_DIR, exist_ok=True)
log_file = os.path.join(CONFIG_DIR, 'victron_monitor.log')
log_handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=5)
log_formatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s')
log_handler.setFormatter(log_formatter)

logging.basicConfig(level=logging.INFO, handlers=[log_handler], format='%(asctime)s %(levelname)s: %(message)s')

# Default settings
DEFAULT_SETTINGS = {
    'TELEGRAM_TOKEN': '',
    'CHAT_ID': '',
    'VICTRON_API_URL': '',
    'API_KEY': '',
    'REFRESH_PERIOD': '5',  # Default refresh period set to 5 seconds
    'MAX_POWER': '',
    'PASSTHRU_CURRENT': '',
    'NOMINAL_VOLTAGE': '230',
    'QUIET_HOURS_START': '',
    'QUIET_HOURS_END': '',
    'QUIET_DAYS': '',
    'TIMEZONE': 'UTC',
    'LANGUAGE': 'en',
    'INSTALLATION_ID': '',
    'VOLTAGE_HIGH_THRESHOLD': '1.10',
    'VOLTAGE_LOW_THRESHOLD': '0.90',
    'TUYA_ACCESS_ID': '',
    'TUYA_ACCESS_KEY': '',
    'TUYA_API_ENDPOINT': '',
    'TUYA_DEVICE_IDS': ''
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
    updated = False
    for key, value in DEFAULT_SETTINGS.items():
        if key not in config['DEFAULT']:
            config['DEFAULT'][key] = value
            updated = True
    if updated:
        save_config(config)
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
    def get_input(prompt, current_value):
        def prefill_input(text):
            def hook():
                readline.insert_text(text)
                readline.redisplay()
            return hook

        readline.set_pre_input_hook(prefill_input(current_value))
        try:
            return input(f"{prompt}: ") or current_value
        finally:
            readline.set_pre_input_hook()  # Reset the hook after input

    config['DEFAULT']['TELEGRAM_TOKEN'] = get_input("Enter Telegram bot token", config['DEFAULT']['TELEGRAM_TOKEN'])
    config['DEFAULT']['CHAT_ID'] = get_input("Enter telegram channel ID (e.g., -1234567890123)", config['DEFAULT']['CHAT_ID'])
    installation_id = get_input("Enter your installation ID (Refer to Victron documentation)", config['DEFAULT']['INSTALLATION_ID'])
    config['DEFAULT']['INSTALLATION_ID'] = installation_id
    config['DEFAULT']['VICTRON_API_URL'] = f"https://vrmapi.victronenergy.com/v2/installations/{installation_id}/diagnostics"
    config['DEFAULT']['API_KEY'] = get_input("Enter Victron API token", config['DEFAULT']['API_KEY'])
    config['DEFAULT']['REFRESH_PERIOD'] = get_input("Enter refresh period in seconds (e.g., 5)", config['DEFAULT']['REFRESH_PERIOD'])
    config['DEFAULT']['MAX_POWER'] = get_input("Enter max output power supported by your device in WATTS (W)", config['DEFAULT']['MAX_POWER'])
    config['DEFAULT']['PASSTHRU_CURRENT'] = get_input("Enter max output current supported by your device in AMPS (A)", config['DEFAULT']['PASSTHRU_CURRENT'])
    config['DEFAULT']['NOMINAL_VOLTAGE'] = get_input("Enter nominal voltage in VOLTS (V)", config['DEFAULT']['NOMINAL_VOLTAGE'])
    config['DEFAULT']['TIMEZONE'] = get_input("Enter timezone (e.g., Europe/Kyiv. Refer to https://www.php.net/manual/en/timezones.php)", config['DEFAULT']['TIMEZONE'])
    config['DEFAULT']['VOLTAGE_HIGH_THRESHOLD'] = get_input("Enter high voltage threshold (e.g., 1.10 for 110%)", config['DEFAULT']['VOLTAGE_HIGH_THRESHOLD'])
    config['DEFAULT']['VOLTAGE_LOW_THRESHOLD'] = get_input("Enter low voltage threshold (e.g., 0.90 for 90%)", config['DEFAULT']['VOLTAGE_LOW_THRESHOLD'])

    save_config(config)
    print("Configuration saved successfully.")

class TuyaController:
    def __init__(self, access_id, access_key, api_endpoint, device_ids):
        self.openapi = TuyaOpenAPI(api_endpoint, access_id, access_key)
        self.openapi.connect()
        self.device_ids = device_ids

    def turn_devices_on(self):
        for device_id in self.device_ids:
            try:
                commands = {'commands': [{'code': 'switch', 'value': True}]}
                response = self.openapi.post(f'/v1.0/iot-03/devices/{device_id}/commands', commands)
                if not response.get('success'):
                    logging.error(f"Failed to turn on device {device_id}: {response.get('msg')}")
            except Exception as e:
                logging.error(f"Exception when turning on device {device_id}: {e}")

    def turn_devices_off(self):
        for device_id in self.device_ids:
            try:
                commands = {'commands': [{'code': 'switch', 'value': False}]}
                response = self.openapi.post(f'/v1.0/iot-03/devices/{device_id}/commands', commands)
                if not response.get('success'):
                    logging.error(f"Failed to turn off device {device_id}: {response.get('msg')}")
            except Exception as e:
                logging.error(f"Exception when turning off device {device_id}: {e}")

def setup_language():
    config = load_config()

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
            'VE_BUS_ERROR_MSG': '🚨 Помилка:\n{error}.\n{timestamp}',
            'VE_BUS_RECOVERY_MSG': '🔧 Система відновлена після помилки.\n{timestamp}',
            'LOW_BATTERY_MSG': '🪫 Низький заряд батареї!\n{timestamp}',
            'CRITICAL_BATTERY_MSG': '‼️🪫 Критичний заряд батареї!\n{timestamp}',
            'VOLTAGE_LOW_MSG': '📉 Вхідна напруга на {phase}-й фазі занадто низька: {voltage}V.\n{timestamp}',
            'VOLTAGE_HIGH_MSG': '📈 Вхідна напруга на {phase}-й фазі занадто висока: {voltage}V.\n{timestamp}',
            'VOLTAGE_NORMAL_MSG': '🆗 Вхідна напруга на {phase}-й фазі в межах норми: {voltage}V.\n{timestamp}',
            'CRITICAL_LOAD_MSG': '‼️ Критичне навантаження на {phase}-й фазі: {power}W.\nЗменшіть споживання.\n{timestamp}',
            'PASSTHRU_MSG': '‼️ Критичне навантаження на {phase}-й фазі: {current}A.\nЗменшіть споживання.\n{timestamp}'
        }
    else:
        messages = {
            'GRID_DOWN_MSG': '⚠️ Grid is down!\n{timestamp}',
            'GRID_UP_MSG': '✅ Grid is restored!\n{timestamp}',
            'VE_BUS_ERROR_MSG': '🚨 Error:\n{error}.\n{timestamp}',
            'VE_BUS_RECOVERY_MSG': '🔧 System recovered from error.\n{timestamp}',
            'LOW_BATTERY_MSG': '🪫 Low battery level!\n{timestamp}',
            'CRITICAL_BATTERY_MSG': '‼️🪫 Critical battery level!\n{timestamp}',
            'VOLTAGE_LOW_MSG': '📉 Input voltage on phase {phase} is too low: {voltage}V.\n{timestamp}',
            'VOLTAGE_HIGH_MSG': '📈 Input voltage on phase {phase} is too high: {voltage}V.\n{timestamp}',
            'VOLTAGE_NORMAL_MSG': '🆗 Input voltage on phase {phase} is within normal range: {voltage}V.\n{timestamp}',
            'CRITICAL_LOAD_MSG': '‼️ Critical load on phase {phase}: {power}W.\nReduce consumption.\n{timestamp}',
            'PASSTHRU_MSG': '‼️ Critical load on phase {phase}: {current}A.\nReduce consumption.\n{timestamp}'
        }

    return messages

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

def view_logs():
    log_file_path = os.path.join(CONFIG_DIR, 'victron_monitor.log')
    if os.path.exists(log_file_path):
        print("\nDisplaying the last 100 lines of the log file:\n")
        with open(log_file_path, 'r') as log_file:
            lines = log_file.readlines()
            print(''.join(lines[-100:]))  # Display the last 100 lines
    else:
        print("Log file not found.")

def setup_quiet_hours():
    config = load_config()
    print("Set Quiet Hours (24h format, hour increments). During this period, messages will be sent silently:")
    start = input("Enter start of quiet hours (e.g., 22) or leave blank to disable: ").strip()

    if start.isdigit():
        start = int(start)
        if 0 <= start <= 23:
            end = input("Enter end of quiet hours (e.g., 8): ").strip()
            if end.isdigit():
                end = int(end)
                if 0 <= end <= 23:
                    config['DEFAULT']['QUIET_HOURS_START'] = str(start)
                    config['DEFAULT']['QUIET_HOURS_END'] = str(end)
                    print(f"Quiet Hours set from {start}:00 to {end}:00")
                else:
                    print("Invalid input for end hour. Please enter a valid hour between 0 and 23.")
                    return
            else:
                print("Invalid input for end hour. Please enter a valid hour between 0 and 23.")
                return
        else:
            print("Invalid input for start hour. Please enter a valid hour between 0 and 23.")
            return
    else:
        config['DEFAULT']['QUIET_HOURS_START'] = ''
        config['DEFAULT']['QUIET_HOURS_END'] = ''
        print("Quiet Hours Disabled")

    print("\nSet Quiet Days (1-7 for Monday-Sunday). On these days, messages will be sent silently all day.")
    days_input = input("Enter quiet days (comma-separated), or leave blank to disable: ").strip()
    if days_input:
        days = [int(day.strip()) for day in days_input.split(',') if day.strip().isdigit() and 1 <= int(day.strip()) <= 7]
        if days:
            config['DEFAULT']['QUIET_DAYS'] = ','.join(str(day) for day in days)
            day_names_map = {1: 'Mon', 2: 'Tue', 3: 'Wed', 4: 'Thu', 5: 'Fri', 6: 'Sat', 7: 'Sun'}
            quiet_days_names = ', '.join(day_names_map.get(day, str(day)) for day in days)
            print(f"Quiet Days set: {quiet_days_names}")
        else:
            print("Invalid input for days. Please enter numbers between 1 and 7, comma-separated.")
            return
    else:
        config['DEFAULT']['QUIET_DAYS'] = ''
        print("Quiet Days Disabled")

    save_config(config)
    print("Quiet Hours and Quiet Days configuration saved successfully.")

def configure_tuya_devices():
    config = load_config()
    settings = config['DEFAULT']

    print("\nConfigure Tuya Devices")
    print("These devices will be switched OFF when the grid is down and switched ON when the grid is restored.")
    print("Only single-switch button Tuya devices are supported, like common DIN-rail switches.")
    print("Leave any input empty to disable Tuya device control.\n")

    def get_input(prompt, current_value):
        def prefill_input(text):
            def hook():
                readline.insert_text(text)
                readline.redisplay()
            return hook

        readline.set_pre_input_hook(prefill_input(current_value))
        try:
            return input(f"{prompt}: ") or ''
        finally:
            readline.set_pre_input_hook()  # Reset the hook after input

    # Prompt for Tuya Access ID
    access_id = get_input("Enter your Tuya Access ID", settings.get('TUYA_ACCESS_ID', '')).strip()
    # Prompt for Tuya Access Key
    access_key = get_input("Enter your Tuya Access Key", settings.get('TUYA_ACCESS_KEY', '')).strip()
    # Prompt for Tuya API Endpoint
    api_endpoint = get_input("Enter your Tuya API Endpoint (e.g., https://openapi.tuyaeu.com)", settings.get('TUYA_API_ENDPOINT', '')).strip()
    # Prompt for Tuya Device IDs
    device_ids = get_input("Enter your Tuya Device IDs (comma-separated)", settings.get('TUYA_DEVICE_IDS', '')).strip()

    # If any required field is empty, disable Tuya device control
    if not all([access_id, access_key, api_endpoint, device_ids]):
        settings['TUYA_ACCESS_ID'] = ''
        settings['TUYA_ACCESS_KEY'] = ''
        settings['TUYA_API_ENDPOINT'] = ''
        settings['TUYA_DEVICE_IDS'] = ''
        print("Tuya device control disabled.")
    else:
        # Save the settings
        settings['TUYA_ACCESS_ID'] = access_id
        settings['TUYA_ACCESS_KEY'] = access_key
        settings['TUYA_API_ENDPOINT'] = api_endpoint
        # Clean and save Device IDs
        settings['TUYA_DEVICE_IDS'] = ','.join([id.strip() for id in device_ids.split(',') if id.strip()])
        print("Tuya configuration saved successfully.")

    save_config(config)
    print("Configuration saved.\n")

def get_service_running_status():
    if is_service_enabled():
        status = subprocess.run(['systemctl', 'is-active', SERVICE_NAME], capture_output=True, text=True)
        if status.stdout.strip() == "active":
            return f"{GREEN_TEXT}Running{RESET_TEXT}"
        else:
            return f"{RED_TEXT}Stopped{RESET_TEXT}"
    else:
        return f"{RED_TEXT}Stopped{RESET_TEXT}"

def restart_service():
    if is_service_enabled():
        try:
            subprocess.run(['sudo', 'systemctl', 'restart', SERVICE_NAME], check=True)
            print("Service restarted successfully.")
        except subprocess.CalledProcessError as e:
            print(f"Failed to restart service: {e}")
    else:
        print("Service is not enabled.")

def is_tuya_configured(config):
    required_keys = [
        'TUYA_ACCESS_ID',
        'TUYA_ACCESS_KEY',
        'TUYA_API_ENDPOINT',
        'TUYA_DEVICE_IDS'
    ]
    return all(config['DEFAULT'].get(key) for key in required_keys)

async def developer_menu():
    global dev_mode
    global reset_last_values
    global simulated_values  # Ensure this is declared global
    dev_mode = True
    reset_last_values = True
    print("Victron API polling is paused. Entering Developer Menu.")
    config = load_config()
    while True:
        print("\nDeveloper Menu - Simulate States")
        print("1. Simulate Grid Down")
        print("2. Simulate Grid Restored")
        print("3. Simulate VE.Bus Error")
        print("4. Simulate VE.Bus Recovered")
        print("5. Simulate Low Battery")
        print("6. Simulate Critical Battery")
        print("7. Simulate Voltage on Phase")
        print("8. Simulate Critical Load")
        print("9. Simulate Passthru Critical Load")
        print("10. Exit Developer Menu")
        choice = (await aioconsole.ainput("Enter your choice (1-10): ")).strip()

        if choice == '1':
            simulated_values['grid_status'] = (2, 'Grid Down')
            print("Simulating Grid Down.")
        elif choice == '2':
            simulated_values['grid_status'] = (0, 'Grid Restored')
            print("Simulating Grid Restored.")
        elif choice == '3':
            simulated_values['ve_bus_status'] = (1, 'VE.Bus Error')
            print("Simulating VE.Bus Error.")
        elif choice == '4':
            simulated_values['ve_bus_status'] = (0, 'No error')
            print("Simulating VE.Bus Recovered.")
        elif choice == '5':
            simulated_values['low_battery_status'] = (1, 'Low Battery')
            print("Simulating Low Battery.")
        elif choice == '6':
            simulated_values['low_battery_status'] = (2, 'Critical Battery')
            print("Simulating Critical Battery.")
        elif choice == '7':
            phase = (await aioconsole.ainput("Enter phase number (1-3): ")).strip()
            voltage_input = (await aioconsole.ainput("Enter desired voltage for the phase: ")).strip()
            try:
                phase = int(phase)
                voltage = float(voltage_input)
                if phase in [1, 2, 3]:
                    simulated_values['voltage_phases'] = simulated_values.get('voltage_phases', {})
                    simulated_values['voltage_phases'][phase] = (voltage, '')
                    print(f"Simulating Voltage {voltage}V on Phase {phase}.")
                else:
                    print("Invalid phase number.")
            except ValueError:
                print("Invalid input. Please enter numeric values for phase and voltage.")
        elif choice == '8':
            phase = (await aioconsole.ainput("Enter phase number (1-3): ")).strip()
            power_input = (await aioconsole.ainput("Enter desired power in W for the phase: ")).strip()
            try:
                phase = int(phase)
                power = float(power_input)
                if phase in [1, 2, 3]:
                    # Get nominal voltage from settings
                    voltage = float(config['DEFAULT']['NOMINAL_VOLTAGE'])
                    current = power / voltage
                    simulated_values['grid_status'] = (2, 'Grid Down')
                    simulated_values['output_voltages'] = simulated_values.get('output_voltages', {})
                    simulated_values['output_currents'] = simulated_values.get('output_currents', {})
                    simulated_values['output_voltages'][phase] = (voltage, '')
                    simulated_values['output_currents'][phase] = (current, '')
                    print(f"Simulating Critical Load on Phase {phase} with Power {power}W.")
                else:
                    print("Invalid phase number.")
            except ValueError:
                print("Invalid input. Please enter numeric values for phase and power.")
        elif choice == '9':
            phase = (await aioconsole.ainput("Enter phase number (1-3): ")).strip()
            current_input = (await aioconsole.ainput("Enter desired current in A for the phase: ")).strip()
            try:
                phase = int(phase)
                current = float(current_input)
                if phase in [1, 2, 3]:
                    simulated_values['ve_bus_state'] = PASSTHRU_STATE
                    simulated_values['grid_status'] = (0, 'Grid Restored')
                    simulated_values['output_currents'] = simulated_values.get('output_currents', {})
                    simulated_values['output_currents'][phase] = (current, '')
                    print(f"Simulating Passthru Critical Load on Phase {phase} with Current {current}A.")
                else:
                    print("Invalid phase number.")
            except ValueError:
                print("Invalid input. Please enter numeric values for phase and current.")
        elif choice == '10':
            dev_mode = False
            reset_last_values = True  # Reset last known states
            simulated_values = {}     # Clear simulated values
            print("Exiting Developer Menu. Victron API polling is resumed.")
            break
        else:
            print("Invalid choice. Please try again.")

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
async def send_telegram_message(bot, CHAT_ID, message, TIMEZONE, is_test_message=False):
    local_tz = pytz.timezone(TIMEZONE)
    now_local = datetime.now(local_tz)
    current_hour = now_local.hour
    current_day = now_local.weekday()  # 0 (Monday) to 6 (Sunday)
    current_day_user_numbering = current_day + 1  # 1 (Monday) to 7 (Sunday)
    config = load_config()
    quiet_hours_start = config['DEFAULT'].getint('QUIET_HOURS_START', fallback=None)
    quiet_hours_end = config['DEFAULT'].getint('QUIET_HOURS_END', fallback=None)
    quiet_days_str = config['DEFAULT'].get('QUIET_DAYS', '')
    if quiet_days_str:
        quiet_days = [int(day.strip()) for day in quiet_days_str.split(',') if day.strip().isdigit()]
    else:
        quiet_days = []

    # Determine if the message should be sent silently
    disable_notification = False

    if current_day_user_numbering in quiet_days:
        disable_notification = True
    else:
        if quiet_hours_start is not None and quiet_hours_end is not None:
            if quiet_hours_start < quiet_hours_end:
                disable_notification = quiet_hours_start <= current_hour < quiet_hours_end
            else:
                disable_notification = current_hour >= quiet_hours_start or current_hour < quiet_hours_end

    if is_test_message:
        message = '👨🏻‍💻 TEST MESSAGE\n' + message

    await bot.send_message(chat_id=CHAT_ID, text=message, disable_notification=disable_notification)

# Monitor loop
async def monitor():
    global dev_mode
    global simulated_values
    global reset_last_values

    last_grid_status = None
    last_ve_bus_status = None
    last_low_battery_status = None
    voltage_issue_reported = {1: False, 2: False, 3: False}
    last_voltage_phases = {1: None, 2: None, 3: None}
    power_issue_counters = {1: 0, 2: 0, 3: 0}
    power_issue_reported = {1: False, 2: False, 3: False}
    first_run = True
    tuya_controller = None

    while True:
        try:
            if reset_last_values:
                last_grid_status = None
                last_ve_bus_status = None
                last_low_battery_status = None
                last_voltage_phases = {1: None, 2: None, 3: None}
                power_issue_counters = {1: 0, 2: 0, 3: 0}
                power_issue_reported = {1: False, 2: False, 3: False}
                voltage_issue_reported = {1: False, 2: False, 3: False}
                reset_last_values = False

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
            except InvalidToken:
                logging.error("Invalid Telegram token provided. Please check your configuration.")
                return

            local_tz = pytz.timezone(TIMEZONE)

            # Fetch the current status from the Victron API or use simulated values
            if dev_mode:
                # Use simulated values
                grid_status = simulated_values.get('grid_status', last_grid_status)
                ve_bus_status = simulated_values.get('ve_bus_status', last_ve_bus_status)
                low_battery_status = simulated_values.get('low_battery_status', last_low_battery_status)
                voltage_phases = simulated_values.get('voltage_phases', last_voltage_phases)
                output_voltages = simulated_values.get('output_voltages', {})
                output_currents = simulated_values.get('output_currents', {})
                ve_bus_state = simulated_values.get('ve_bus_state', None)
                # Simulated timestamp
                timestamp = datetime.now(local_tz).strftime("%d.%m.%Y %H:%M")
            else:
                # Fetch from API
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

            # Initialize TuyaController if credentials are available
            tuya_enabled = all([
                settings.get('TUYA_ACCESS_ID'),
                settings.get('TUYA_ACCESS_KEY'),
                settings.get('TUYA_API_ENDPOINT'),
                settings.get('TUYA_DEVICE_IDS')
            ])

            if tuya_enabled and tuya_controller is None:
                tuya_device_ids = [id.strip() for id in settings['TUYA_DEVICE_IDS'].split(',')]
                tuya_controller = TuyaController(
                    settings['TUYA_ACCESS_ID'],
                    settings['TUYA_ACCESS_KEY'],
                    settings['TUYA_API_ENDPOINT'],
                    tuya_device_ids
                )
                logging.info("Tuya Controller initialized.")
            elif not tuya_enabled:
                tuya_controller = None
                logging.info("Tuya configuration not found. Skipping Tuya device control.")

            # Check and send grid status updates
            if grid_status != last_grid_status:
                if grid_status and grid_status[0] == 2:
                    message = messages['GRID_DOWN_MSG'].replace('{timestamp}', timestamp)
                    await send_telegram_message(bot, CHAT_ID, message, TIMEZONE, is_test_message=dev_mode)

                    # Turn off Tuya devices
                    if tuya_controller:
                        try:
                            tuya_controller.turn_devices_off()
                            logging.info("Tuya devices turned off due to grid down.")
                        except Exception as e:
                            logging.error(f"Error turning off Tuya devices: {e}")

                elif grid_status and grid_status[0] == 0:
                    # Grid is restored
                    message = messages['GRID_UP_MSG'].replace('{timestamp}', timestamp)
                    await send_telegram_message(bot, CHAT_ID, message, TIMEZONE, is_test_message=dev_mode)

                    # Turn on Tuya devices
                    if tuya_controller:
                        try:
                            tuya_controller.turn_devices_on()
                            logging.info("Tuya devices turned on due to grid restoration.")
                        except Exception as e:
                            logging.error(f"Error turning on Tuya devices: {e}")
                last_grid_status = grid_status

            # Check and send VE.Bus error updates independently
            if ve_bus_status is not None and ve_bus_status != last_ve_bus_status:
                if ve_bus_status[1] == "No error":
                    message = messages['VE_BUS_RECOVERY_MSG'].replace('{timestamp}', timestamp)
                    await send_telegram_message(bot, CHAT_ID, message, TIMEZONE, is_test_message=dev_mode)
                else:
                    message = messages['VE_BUS_ERROR_MSG'].replace('{error}', ve_bus_status[1]).replace('{timestamp}', timestamp)
                    await send_telegram_message(bot, CHAT_ID, message, TIMEZONE, is_test_message=dev_mode)
                last_ve_bus_status = ve_bus_status

            # Check and send low battery status updates independently
            if low_battery_status is not None and low_battery_status != last_low_battery_status:
                if last_low_battery_status is None or last_low_battery_status[0] == 0:
                    if low_battery_status[0] == 1:
                        message = messages['LOW_BATTERY_MSG'].replace('{timestamp}', timestamp)
                        await send_telegram_message(bot, CHAT_ID, message, TIMEZONE, is_test_message=dev_mode)
                    elif low_battery_status[0] == 2:
                        message = messages['CRITICAL_BATTERY_MSG'].replace('{timestamp}', timestamp)
                        await send_telegram_message(bot, CHAT_ID, message, TIMEZONE, is_test_message=dev_mode)
                elif last_low_battery_status[0] == 1 and low_battery_status[0] == 2:
                    message = messages['CRITICAL_BATTERY_MSG'].replace('{timestamp}', timestamp)
                    await send_telegram_message(bot, CHAT_ID, message, TIMEZONE, is_test_message=dev_mode)

                last_low_battery_status = low_battery_status

            # Check and send voltage phase updates independently
            for phase in range(1, 4):
                voltage = voltage_phases.get(phase)
                last_voltage = last_voltage_phases.get(phase)

                # Load nominal voltage and calculate thresholds
                nominal_voltage = float(settings['NOMINAL_VOLTAGE'])
                voltage_low_threshold = nominal_voltage * float(settings['VOLTAGE_LOW_THRESHOLD'])
                voltage_high_threshold = nominal_voltage * float(settings['VOLTAGE_HIGH_THRESHOLD'])
                voltage_normal_low = nominal_voltage * 0.955  # 4.5% less than nominal voltage
                voltage_normal_high = nominal_voltage * 1.045  # 4.5% more than nominal voltage

                if voltage is not None and voltage[0] > 0:  # Check if voltage (rawValue) is greater than 0
                    # Handle low voltage
                    if voltage[0] < voltage_low_threshold and not voltage_issue_reported[phase]:
                        message = messages['VOLTAGE_LOW_MSG'].replace('{phase}', str(phase)).replace('{voltage}', f"{voltage[0]:.1f}").replace('{timestamp}', timestamp)
                        await send_telegram_message(bot, CHAT_ID, message, TIMEZONE, is_test_message=dev_mode)
                        voltage_issue_reported[phase] = True

                    # Handle high voltage
                    elif voltage[0] > voltage_high_threshold and not voltage_issue_reported[phase]:
                        message = messages['VOLTAGE_HIGH_MSG'].replace('{phase}', str(phase)).replace('{voltage}', f"{voltage[0]:.1f}").replace('{timestamp}', timestamp)
                        await send_telegram_message(bot, CHAT_ID, message, TIMEZONE, is_test_message=dev_mode)
                        voltage_issue_reported[phase] = True

                    # Handle voltage back to normal range
                    elif voltage_normal_low <= voltage[0] <= voltage_normal_high and voltage_issue_reported[phase]:
                        message = messages['VOLTAGE_NORMAL_MSG'].replace('{phase}', str(phase)).replace('{voltage}', f"{voltage[0]:.1f}").replace('{timestamp}', timestamp)
                        await send_telegram_message(bot, CHAT_ID, message, TIMEZONE, is_test_message=dev_mode)
                        voltage_issue_reported[phase] = False

                last_voltage_phases[phase] = voltage

            # Check power consumption on each phase if the grid is absent
            if grid_status and grid_status[0] == 2:  # Assuming grid_status[0] == 2 means grid is down
                for phase in range(1, 4):
                    if output_voltages.get(phase) is not None and output_currents.get(phase) is not None:
                        # Calculate power consumption by multiplying voltage and current
                        power = output_voltages[phase][0] * output_currents[phase][0]
                        max_power = float(settings['MAX_POWER'])
                        power_limit = max_power * 0.98  # 2% less than MAX_POWER
                        power_reset_threshold = max_power * 0.80  # 20% less than MAX_POWER

                        if power > power_limit:
                            power_issue_counters[phase] += 1
                            if power_issue_counters[phase] >= 2 and not power_issue_reported[phase]:  # If power > power_limit for 2 refreshes
                                logging.info(f"Phase {phase} - MAX POWER ALERT TRIGGERED! Voltage: {output_voltages[phase][0]}V, Current: {output_currents[phase][0]}A, Power: {power:.2f}W")
                                message = messages['CRITICAL_LOAD_MSG'].replace('{phase}', str(phase)).replace('{power}', f"{power:.2f}").replace('{timestamp}', timestamp)
                                await send_telegram_message(bot, CHAT_ID, message, TIMEZONE, is_test_message=dev_mode)
                                power_issue_reported[phase] = True
                        elif power < power_reset_threshold:
                            power_issue_counters[phase] = 0
                            power_issue_reported[phase] = False
                    else:
                        power_issue_counters[phase] = 0

            # Check power consumption on each phase if the VE.Bus state is "Passthru"
            if ve_bus_state != PASSTHRU_STATE:
                for phase in range(1, 4):
                    if output_voltages.get(phase) is not None and output_currents.get(phase) is not None:
                        current = output_currents[phase][0]

                        passthru_current_limit = float(settings['PASSTHRU_CURRENT']) * 0.98  # 2% less than PASSTHRU_CURRENT
                        passthru_current_reset_threshold = float(settings['PASSTHRU_CURRENT']) * 0.85  # 15% less than PASSTHRU_CURRENT

                        if current > passthru_current_limit:
                            power_issue_counters[phase] += 1
                            if power_issue_counters[phase] >= 2 and not power_issue_reported[phase]:
                                logging.info(f"Phase {phase} - PASSTHRU MAX CURRENT ALERT TRIGGERED! Voltage: {output_voltages[phase][0]}V, Current: {current:.2f}A")
                                message = messages['PASSTHRU_MSG'].replace('{phase}', str(phase)).replace('{current}', f"{current:.2f}").replace('{timestamp}', timestamp)
                                await send_telegram_message(bot, CHAT_ID, message, TIMEZONE, is_test_message=dev_mode)
                                power_issue_reported[phase] = True
                        elif current < passthru_current_reset_threshold:
                            power_issue_counters[phase] = 0
                            power_issue_reported[phase] = False
                    else:
                        power_issue_counters[phase] = 0

            await asyncio.sleep(REFRESH_PERIOD)
        except Exception as e:
            logging.error(f"Error: {e}")
            await asyncio.sleep(REFRESH_PERIOD)

# Main menu
async def main():
    config = load_config()
    if not sys.stdin.isatty():
        print("Running in non-interactive mode. Starting monitor.")
        await monitor()
        return

    # Start the monitor function as a background task
    asyncio.create_task(monitor())

    while True:
        config = load_config()
        quiet_hours_info = ""
        if config['DEFAULT']['QUIET_HOURS_START'] and config['DEFAULT']['QUIET_HOURS_END']:
            quiet_hours_info = f"{config['DEFAULT']['QUIET_HOURS_START']}:00 to {config['DEFAULT']['QUIET_HOURS_END']}:00"
        else:
            quiet_hours_info = "Disabled"

        if config['DEFAULT']['QUIET_DAYS']:
            day_names_map = {1: 'Mon', 2: 'Tue', 3: 'Wed', 4: 'Thu', 5: 'Fri', 6: 'Sat', 7: 'Sun'}
            quiet_days_numbers = [int(day) for day in config['DEFAULT']['QUIET_DAYS'].split(',')]
            quiet_days_names = ', '.join(day_names_map.get(day, str(day)) for day in quiet_days_numbers)
            quiet_days_info = f"{quiet_days_names}"
        else:
            quiet_days_info = "Disabled"

        quiet_hours_status = f"Quiet Hours: {quiet_hours_info}, Quiet Days: {quiet_days_info}"

        current_language = config['DEFAULT'].get('LANGUAGE', 'en')
        language_name = "Українська" if current_language == 'uk' else "English"

        service_running_status = get_service_running_status()

        service_status = "(Enabled)" if is_service_enabled() else "(Disabled)"

        tuya_configured = is_tuya_configured(config)
        tuya_status = "(Configured)" if tuya_configured else "(Not Configured)"

        print(f"Status: {service_running_status}")
        print("Please choose an option:")
        print(f"1. Configuration")
        print(f"2. Enable or disable service at startup {service_status}")
        print(f"3. Message language ({language_name})")
        print(f"4. Set Quiet Hours and Quiet Days ({quiet_hours_status})")
        print(f"5. Configure Tuya Devices {tuya_status}")
        print("6. Restart Service")
        print("7. View Logs")
        print("8. Developer Menu")
        print("9. Exit")

        choice = input("Enter your choice (1-9): ")

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
            configure_tuya_devices()
        elif choice == '6':
            restart_service()
        elif choice == '7':
            view_logs()
        elif choice == '8':
            await developer_menu()
        elif choice == '9':
            sys.exit(0)
        else:
            print("Invalid choice. Please try again.")

if __name__ == '__main__':
    asyncio.run(main())