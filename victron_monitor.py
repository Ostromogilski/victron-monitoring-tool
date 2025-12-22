#!/usr/bin/env python3

import configparser
import os
import sys
import requests
import asyncio
from telegram import Bot
from telegram.error import InvalidToken
from datetime import datetime, timedelta, time as dt_time
import json
import re
import tempfile
import pytz
import subprocess
import logging
from logging.handlers import RotatingFileHandler
import readline
from tuya_connector import TuyaOpenAPI
import aioconsole
import time

try:
    from telethon import TelegramClient
    from telethon.tl.types import MessageMediaPhoto
except ImportError:  # pragma: no cover
    TelegramClient = None
    MessageMediaPhoto = None

try:
    import replicate
except ImportError:  # pragma: no cover
    replicate = None

# Global variables
dev_mode = False
simulated_values = {}
reset_last_values = False
last_grid_status = None
last_ve_bus_status = None
last_low_battery_status = None
voltage_issue_reported = {1: False, 2: False, 3: False}
last_voltage_phases = {1: None, 2: None, 3: None}
power_issue_counters = {1: 0, 2: 0, 3: 0}
power_issue_reported = {1: False, 2: False, 3: False}
last_soc = None
battery_low_reported = False
battery_critical_reported = False
schedule_login_in_progress = False
dtek_schedule_cache = {}
dtek_schedule_last_updated = None

# Configuration
CONFIG_DIR = os.path.expanduser('~/victron_monitor/')
CONFIG_FILE = os.path.join(CONFIG_DIR, 'settings.ini')
DTEK_SCHEDULE_CACHE_FILE = os.path.join(CONFIG_DIR, 'dtek_schedule_cache.json')
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
SOC_ID = 51
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
    'BATTERY_LOW_SOC_THRESHOLD': '20',
    'BATTERY_CRITICAL_SOC_THRESHOLD': '10',
    'TUYA_ACCESS_ID': '',
    'TUYA_ACCESS_KEY': '',
    'TUYA_API_ENDPOINT': '',
    'TUYA_DEVICE_IDS': '',
    'SCHEDULE_ENABLED': '',
    'DTEK_TELEGRAM_API_ID': '',
    'DTEK_TELEGRAM_API_HASH': '',
    'DTEK_TELEGRAM_SESSION_NAME': 'dtek_schedule_session',
    'DTEK_CHANNEL': 'dtek_ua',
    'DTEK_QUEUE': '3.1',
    'REPLICATE_API_TOKEN': '',
    'SCHEDULE_REFRESH_MINUTES': '60',
    'PRE_OUTAGE_TUYA_OFF_MINUTES': '5',
    'LOG_LEVEL': 'INFO'
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

# Function to set up logging level
def setup_logging():
    # Set up logging with rotation
    os.makedirs(CONFIG_DIR, exist_ok=True)
    log_file = os.path.join(CONFIG_DIR, 'victron_monitor.log')
    log_handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=5)
    log_formatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s')
    log_handler.setFormatter(log_formatter)
    logging.basicConfig(level=logging.INFO, handlers=[log_handler], format='%(asctime)s %(levelname)s: %(message)s')

    # After loading the config, adjust the logging level
    config = load_config()
    logging_level_str = config['DEFAULT'].get('LOG_LEVEL', 'INFO').upper()
    logging_level = getattr(logging, logging_level_str, logging.INFO)
    logging.getLogger().setLevel(logging_level)

# Call the setup_logging function to initialize logging
setup_logging()

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
    config['DEFAULT']['BATTERY_LOW_SOC_THRESHOLD'] = get_input("Enter low battery SOC threshold (%)", config['DEFAULT']['BATTERY_LOW_SOC_THRESHOLD'])
    config['DEFAULT']['BATTERY_CRITICAL_SOC_THRESHOLD'] = get_input("Enter critical battery SOC threshold (%)", config['DEFAULT']['BATTERY_CRITICAL_SOC_THRESHOLD'])

    schedule_enabled_raw = get_input(
        "Enable DTEK schedule automation (turn off Tuya before scheduled outages) (y/n)",
        'y' if config['DEFAULT'].get('SCHEDULE_ENABLED', '') else 'n'
    ).strip().lower()
    schedule_enabled = schedule_enabled_raw in ('y', 'yes', '1', 'true', 'on')
    config['DEFAULT']['SCHEDULE_ENABLED'] = 'y' if schedule_enabled else ''

    if schedule_enabled:
        config['DEFAULT']['DTEK_TELEGRAM_API_ID'] = get_input(
            "Enter Telegram API ID (Telethon)",
            config['DEFAULT'].get('DTEK_TELEGRAM_API_ID', '')
        )
        config['DEFAULT']['DTEK_TELEGRAM_API_HASH'] = get_input(
            "Enter Telegram API Hash (Telethon)",
            config['DEFAULT'].get('DTEK_TELEGRAM_API_HASH', '')
        )
        config['DEFAULT']['DTEK_TELEGRAM_SESSION_NAME'] = get_input(
            "Enter Telegram session name", 
            config['DEFAULT'].get('DTEK_TELEGRAM_SESSION_NAME', 'dtek_schedule_session')
        )
        config['DEFAULT']['DTEK_CHANNEL'] = get_input(
            "Enter DTEK Telegram channel (e.g., dtek_ua)",
            config['DEFAULT'].get('DTEK_CHANNEL', 'dtek_ua')
        )
        config['DEFAULT']['DTEK_QUEUE'] = get_input(
            "Enter DTEK queue (e.g., 3.1)",
            config['DEFAULT'].get('DTEK_QUEUE', '3.1')
        )
        config['DEFAULT']['REPLICATE_API_TOKEN'] = get_input(
            "Enter Replicate API token", 
            config['DEFAULT'].get('REPLICATE_API_TOKEN', '')
        )
        config['DEFAULT']['SCHEDULE_REFRESH_MINUTES'] = get_input(
            "Enter schedule refresh interval in minutes (e.g., 60)",
            config['DEFAULT'].get('SCHEDULE_REFRESH_MINUTES', '60')
        )
        config['DEFAULT']['PRE_OUTAGE_TUYA_OFF_MINUTES'] = get_input(
            "Enter minutes before outage to turn off Tuya devices (e.g., 5)",
            config['DEFAULT'].get('PRE_OUTAGE_TUYA_OFF_MINUTES', '5')
        )
    else:
        config['DEFAULT']['DTEK_TELEGRAM_API_ID'] = ''
        config['DEFAULT']['DTEK_TELEGRAM_API_HASH'] = ''
        config['DEFAULT']['REPLICATE_API_TOKEN'] = ''

    save_config(config)
    print("Configuration saved successfully.")

class TuyaController:
    def __init__(self, access_id, access_key, api_endpoint, device_ids):
        self.access_id = access_id
        self.access_key = access_key
        self.api_endpoint = api_endpoint
        self.device_ids = [device_id.strip() for device_id in device_ids.split(',') if device_id.strip()]
        self.openapi = TuyaOpenAPI(api_endpoint, access_id, access_key)
        self.openapi.connect()

    def reauthenticate(self):
        try:
            self.openapi = TuyaOpenAPI(self.api_endpoint, self.access_id, self.access_key)
            self.openapi.connect()
            logging.info("Re-authenticated with Tuya API.")
        except Exception as e:
            logging.error(f"Failed to re-authenticate: {e}")

    async def send_command_async(self, device_id, commands):
        # Run the Tuya API call in a thread to avoid blocking
        response = await asyncio.to_thread(self.openapi.post, f'/v1.0/iot-03/devices/{device_id}/commands', commands)
        if not response.get('success'):
            if response.get('code') == 'TOKEN_INVALID':
                logging.warning("Token invalid, re-authenticating.")
                self.reauthenticate()
                response = await asyncio.to_thread(self.openapi.post, f'/v1.0/iot-03/devices/{device_id}/commands', commands)
                if not response.get('success'):
                    logging.error(f"Failed to send command to device {device_id} after re-authentication: {response.get('msg')}")
                else:
                    logging.info(f"Successfully sent command to device {device_id} after re-authentication.")
            else:
                logging.error(f"Failed to send command to device {device_id}: {response.get('msg')}")
        else:
            logging.info(f"Successfully sent command to device {device_id}.")

    async def get_device_status_async(self, device_id):
        response = await asyncio.to_thread(self.openapi.get, f'/v1.0/iot-03/devices/{device_id}/status')
        if response.get('success'):
            return response.get('result')
        else:
            logging.error(f"Failed to get status for device {device_id}: {response.get('msg')}")
            return None

    async def verify_device_state_async(self, device_id, desired_state, delay=2):
        """
        Verify that the device reached the desired state.
        
        :param device_id: The Tuya device ID
        :param desired_state: True if we want the device ON, False if OFF
        :param delay: Seconds to wait between checks
        :return: True if desired state is achieved, False otherwise
        """
        status = await self.get_device_status_async(device_id)
        if status:
            # Find the 'switch' status in the returned list
            switch_status = next((item for item in status if item['code'] == 'switch'), None)
            if switch_status and switch_status['value'] == desired_state:
                logging.info(f"Device {device_id} state verified: {desired_state}")
                return True
            else:
                logging.warning(f"Device {device_id} not in desired state yet.")
        else:
            logging.error(f"Unable to verify device {device_id} state (no status returned).")
        await asyncio.sleep(delay)
        return False

    async def ensure_desired_state(self, device_id, commands, desired_state, max_retries=100, verification_delay=2):
        """
        Attempt to set the device to the desired state, verify it, and if it fails,
        re-send the command up to max_retries times.
        
        :param device_id: The Tuya device ID
        :param commands: The command payload to send
        :param desired_state: True if we want the device ON, False if OFF
        :param max_retries: Maximum number of attempts to set the desired state
        :param verification_delay: Delay between verification attempts
        """
        for attempt in range(1, max_retries + 1):
            logging.info(f'Attempt {attempt}/{max_retries} to set device {device_id} to {desired_state}')
            await self.send_command_async(device_id, commands)
            success = await self.verify_device_state_async(device_id, desired_state, delay=verification_delay)
            if success:
                return
            else:
                if attempt < max_retries:
                    logging.warning(f'Retrying to set device {device_id} to {desired_state}.')
                else:
                    logging.error(f'Failed to set device {device_id} to {desired_state} after {max_retries} attempts.')

    async def turn_devices_on(self):
        desired_state = True
        commands = {'commands': [{'code': 'switch', 'value': desired_state}]}
        await asyncio.gather(*(self.ensure_desired_state(device_id, commands, desired_state) for device_id in self.device_ids))

    async def turn_devices_off(self):
        desired_state = False
        commands = {'commands': [{'code': 'switch', 'value': desired_state}]}
        await asyncio.gather(*(self.ensure_desired_state(device_id, commands, desired_state) for device_id in self.device_ids))

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
            'LOW_BATTERY_MSG': '🪫 Низький рівень заряду батареї: {soc}%!\n{timestamp}',
            'CRITICAL_BATTERY_MSG': '‼️🪫 Критичний рівень заряду батареї: {soc}%!\n{timestamp}',
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
            'LOW_BATTERY_MSG': '🪫 Low battery level: {soc}%!\n{timestamp}',
            'CRITICAL_BATTERY_MSG': '‼️🪫 Critical battery level: {soc}%!\n{timestamp}',
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
            return f"🟢 {GREEN_TEXT}Running{RESET_TEXT}"
        else:
            return f"🛑 {RED_TEXT}Stopped{RESET_TEXT}"
    else:
        return f"🛑 {RED_TEXT}Stopped{RESET_TEXT}"

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
    global simulated_values
    global last_grid_status, last_ve_bus_status, last_low_battery_status, last_voltage_phases
    global power_issue_counters, power_issue_reported, voltage_issue_reported

    # Store the real states before starting simulations
    stored_grid_status = last_grid_status
    stored_ve_bus_status = last_ve_bus_status
    stored_low_battery_status = last_low_battery_status
    stored_voltage_phases = last_voltage_phases.copy()
    stored_power_issue_counters = power_issue_counters.copy()
    stored_power_issue_reported = power_issue_reported.copy()
    stored_voltage_issue_reported = voltage_issue_reported.copy()

    dev_mode = True
    print("Victron API polling is paused. Entering Developer Menu.")
    config = load_config()
    REFRESH_PERIOD = int(config['DEFAULT'].get('REFRESH_PERIOD', 5))

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
        print("10. Send DTEK Schedule Update Message (cached)")
        print("11. Exit Developer Menu")
        choice = (await aioconsole.ainput("Enter your choice (1-11): ")).strip()

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
            # Simulate Critical Load on Phase
            simulated_values['grid_status'] = (2, 'Grid Down')
            print("Simulating Grid Down.")

            await asyncio.sleep(REFRESH_PERIOD + 1)

            phase = 1
            max_power = float(config['DEFAULT']['MAX_POWER'])
            power_limit = max_power * 0.98
            power = power_limit + 100  # Exceed the threshold by 100W

            voltage = float(config['DEFAULT']['NOMINAL_VOLTAGE'])
            current = power / voltage
            simulated_values['output_voltages'] = simulated_values.get('output_voltages', {})
            simulated_values['output_currents'] = simulated_values.get('output_currents', {})
            simulated_values['output_voltages'][phase] = (voltage, '')
            simulated_values['output_currents'][phase] = (current, '')
            print(f"Simulating Critical Load on Phase {phase} with Power {power:.2f}W.")

            await asyncio.sleep((REFRESH_PERIOD + 1) * 5)

            # Restore the real values for voltage and current on the phase instead of setting None
            simulated_values['output_voltages'][phase] = stored_voltage_phases.get(phase)
            simulated_values['output_currents'][phase] = stored_voltage_phases.get(phase)  # Assuming the same logic for currents
            simulated_values['grid_status'] = stored_grid_status
            print(f"Ending Critical Load simulation on Phase {phase}.")
        elif choice == '9':
            simulated_values['grid_status'] = (0, 'Grid Restored')
            print("Simulating Grid Restored.")
            simulated_values['ve_bus_state'] = PASSTHRU_STATE
            print("Simulating VE.Bus in Passthru State.")

            await asyncio.sleep(REFRESH_PERIOD + 1)

            phase = 1
            passthru_current = float(config['DEFAULT']['PASSTHRU_CURRENT'])
            current_limit = passthru_current * 0.98
            current = current_limit + 1  # Exceed the threshold by 1A

            voltage = float(config['DEFAULT']['NOMINAL_VOLTAGE'])  # Use nominal voltage

            simulated_values['output_voltages'] = simulated_values.get('output_voltages', {})
            simulated_values['output_currents'] = simulated_values.get('output_currents', {})
            simulated_values['output_voltages'][phase] = (voltage, '')
            simulated_values['output_currents'][phase] = (current, '')
            print(f"Simulating Passthru Critical Load on Phase {phase} with Current {current:.2f}A.")

            await asyncio.sleep((REFRESH_PERIOD + 1) * 5)

            # Restore the real values for voltage and current on the phase instead of setting None
            simulated_values['output_voltages'][phase] = stored_voltage_phases.get(phase)
            simulated_values['output_currents'][phase] = stored_voltage_phases.get(phase)  # Assuming the same logic for currents
            simulated_values['ve_bus_state'] = stored_ve_bus_status
            simulated_values['grid_status'] = stored_grid_status
            print(f"Ending Passthru Critical Load simulation on Phase {phase}.")
        elif choice == '10':
            config = load_config()
            settings = config['DEFAULT']
            TELEGRAM_TOKEN = settings.get('TELEGRAM_TOKEN', '')
            CHAT_ID = settings.get('CHAT_ID', '')
            TIMEZONE = settings.get('TIMEZONE', 'UTC')

            if not TELEGRAM_TOKEN or not CHAT_ID:
                print("Telegram token/chat_id are not configured. Configure them first.")
                continue

            try:
                bot = Bot(token=TELEGRAM_TOKEN)
            except InvalidToken:
                print("Invalid Telegram token provided. Please check your configuration.")
                continue

            if not dtek_schedule_cache:
                _load_dtek_schedule_cache_from_disk()

            tz = pytz.timezone(TIMEZONE)
            now = datetime.now(tz)
            sent_any = False
            for day_offset in range(2):
                date_str = (now + timedelta(days=day_offset)).date().strftime('%Y-%m-%d')
                schedule = dtek_schedule_cache.get(date_str)
                if not schedule:
                    continue
                try:
                    msg = _format_dtek_schedule_update_html(schedule)
                    await send_telegram_message(bot, CHAT_ID, msg, TIMEZONE, is_test_message=dev_mode, parse_mode='HTML')
                    sent_any = True
                except Exception as e:
                    print(f"Failed to send schedule update message: {e}")

            if sent_any:
                print("Schedule update message sent.")
            else:
                print("No cached schedule found for today/tomorrow.")
        elif choice == '11':
            # Exit Simulation and restore real values
            dev_mode = False
            simulated_values = {}

            # Restore the real values that were stored before simulation
            last_grid_status = stored_grid_status
            last_ve_bus_status = stored_ve_bus_status
            last_low_battery_status = stored_low_battery_status
            last_voltage_phases = stored_voltage_phases.copy()
            power_issue_counters = stored_power_issue_counters.copy()
            power_issue_reported = stored_power_issue_reported.copy()
            voltage_issue_reported = stored_voltage_issue_reported.copy()

            print("Simulation ended. Restored real values and resuming Victron API polling.")
            break
        else:
            print("Invalid choice. Please try again.")

def setup_logging_level():
    config = load_config()
    current_level = config['DEFAULT'].get('LOG_LEVEL', 'INFO').upper()
    print(f"Current logging level: {current_level}")
    print("Select logging level:")
    print("1. DEBUG")
    print("2. INFO")
    print("3. WARNING")
    print("4. ERROR")
    print("5. CRITICAL")
    choice = input("Enter your choice (1-5): ").strip()
    level_map = {
        '1': 'DEBUG',
        '2': 'INFO',
        '3': 'WARNING',
        '4': 'ERROR',
        '5': 'CRITICAL'
    }
    new_level = level_map.get(choice)
    if new_level:
        config['DEFAULT']['LOG_LEVEL'] = new_level
        save_config(config)
        logging_level = getattr(logging, new_level, logging.INFO)
        logging.getLogger().setLevel(logging_level)
        print(f"Logging level set to {new_level}")
    else:
        print("Invalid choice. Logging level not changed.")

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
        grid_status, ve_bus_status, soc = None, None, None
        voltage_phases = {1: None, 2: None, 3: None}
        output_voltages = {1: None, 2: None, 3: None}
        output_currents = {1: None, 2: None, 3: None}
        ve_bus_state = None

        if 'records' in diagnostics:
            for diagnostic in diagnostics['records']:
                if diagnostic['idDataAttribute'] == GRID_ALARM_ID:
                    grid_status = int(diagnostic['rawValue']), diagnostic['formattedValue']
                elif diagnostic['idDataAttribute'] == VE_BUS_ERROR_ID:
                    ve_bus_status = int(diagnostic['rawValue']), diagnostic['formattedValue']
                elif diagnostic['idDataAttribute'] == VE_BUS_STATE_ID:
                    ve_bus_state = int(diagnostic['rawValue'])
                elif diagnostic['idDataAttribute'] == SOC_ID:
                    soc = float(diagnostic['rawValue'])
                elif diagnostic['idDataAttribute'] in [VOLTAGE_PHASE_1_ID, VOLTAGE_PHASE_2_ID, VOLTAGE_PHASE_3_ID]:
                    phase = diagnostic['idDataAttribute'] - 7
                    voltage_phases[phase] = float(diagnostic['rawValue']), diagnostic['formattedValue']
                elif diagnostic['idDataAttribute'] in [OUTPUT_VOLTAGE_PHASE_1_ID, OUTPUT_VOLTAGE_PHASE_2_ID, OUTPUT_VOLTAGE_PHASE_3_ID]:
                    phase = diagnostic['idDataAttribute'] - 19
                    output_voltages[phase] = float(diagnostic['rawValue']), diagnostic['formattedValue']
                elif diagnostic['idDataAttribute'] in [OUTPUT_CURRENT_PHASE_1_ID, OUTPUT_CURRENT_PHASE_2_ID, OUTPUT_CURRENT_PHASE_3_ID]:
                    phase = diagnostic['idDataAttribute'] - 22
                    output_currents[phase] = float(diagnostic['rawValue']), diagnostic['formattedValue']

        return grid_status, ve_bus_status, soc, voltage_phases, output_voltages, output_currents, ve_bus_state
    except ValueError as e:
        logging.error(f"Error parsing JSON: {e}")
        return None, None, None, None, None, None, None
    except Exception as e:
        logging.error(f"Unexpected error in get_status(): {e}")
        return None, None, None, None, None, None, None

def _parse_hhmm(value: str):
    value = (value or '').strip()
    if value == '24:00':
        return dt_time(0, 0)
    parts = value.split(':')
    if len(parts) != 2:
        raise ValueError('Invalid time')
    return dt_time(int(parts[0]), int(parts[1]))

class DtekScheduleFetcher:
    def __init__(self, api_id: int, api_hash: str, session_name: str, channel: str, queue: str, replicate_api_token: str):
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_name = session_name
        self.channel = channel
        self.queue = queue
        self.replicate_api_token = replicate_api_token
        self.client = None
        self.replicate_client = replicate.Client(api_token=replicate_api_token) if replicate is not None and replicate_api_token else None

    async def _ensure_client(self):
        if TelegramClient is None:
            return None
        if not self.api_id or not self.api_hash:
            return None
        if self.client is None:
            session_name = (self.session_name or '').strip() or 'dtek_schedule_session'
            if not os.path.isabs(session_name):
                session_name = os.path.join(CONFIG_DIR, session_name)
            self.client = TelegramClient(session_name, self.api_id, self.api_hash)
        if not self.client.is_connected():
            await self.client.connect()

        try:
            is_auth = await self.client.is_user_authorized()
        except Exception:
            is_auth = False

        if not is_auth:
            if sys.stdin.isatty():
                await self.client.start()
            else:
                logging.error("DTEK schedule: Telethon session is not authorized. Run victron_monitor interactively once to log in, then restart the service.")
                return None
        return self.client

    def _parse_schedule_json(self, text: str, tz):
        text = (text or '').strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                return None
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                return None

        if not isinstance(data, dict):
            return None
        if 'date' not in data or 'periods' not in data:
            return None

        date_str = str(data.get('date', '')).strip()
        try:
            parsed_date = datetime.strptime(date_str, '%Y-%m-%d')
            current_year = datetime.now(tz).year
            if parsed_date.year < current_year - 1:
                data['date'] = parsed_date.replace(year=current_year).strftime('%Y-%m-%d')
        except Exception:
            return None

        queue_str = str(data.get('queue', '') or '').strip()
        if queue_str and queue_str != str(self.queue):
            return None
        data['queue'] = str(self.queue)
        if not isinstance(data.get('periods'), list):
            data['periods'] = []
        return data

    async def fetch_latest_schedule(self, tz):
        client = await self._ensure_client()
        if client is None or self.replicate_client is None:
            return None

        messages = await client.get_messages(self.channel, limit=50)
        if not messages:
            return None

        kyiv_msg = None
        for msg in messages:
            text = (getattr(msg, 'message', '') or '')
            if 'Київ:' in text and 'графік' in text:
                kyiv_msg = msg
                break
        if kyiv_msg is None:
            return None

        if kyiv_msg.grouped_id:
            album = [m for m in messages if m.grouped_id == kyiv_msg.grouped_id]
            album.sort(key=lambda m: m.id)
        else:
            album = [kyiv_msg]

        photos = [m for m in album if getattr(m, 'photo', None) or (MessageMediaPhoto is not None and isinstance(getattr(m, 'media', None), MessageMediaPhoto))]
        if not photos:
            return None

        photo_msgs = photos[:2]
        current_year = datetime.now(tz).year
        prompt = (
            "You are an assistant that reads Ukrainian power outage schedules from images. "
            "The image contains a table with outage schedules for several queues. "
            f"You must read ONLY the row for 'Черга {self.queue}' in the city of Kyiv and return its outage schedule. "
            f"IMPORTANT: The current year is {current_year}. Make sure the date in YYYY-MM-DD format uses the correct year {current_year}. "
            "Return STRICTLY one JSON object with no extra text in the following format: "
            f"{{\"date\": \"YYYY-MM-DD\", \"queue\": \"{self.queue}\", \"periods\": [[\"HH:MM\", \"HH:MM\"], ...]}} . "
            "If there are no outages for this queue on that date, return \"periods\": []. "
            "If the row for this queue is not present in this image or you cannot read it clearly, do NOT guess: return {\"date\": null, \"queue\": \""
            + str(self.queue)
            + "\", \"periods\": []}."
        )

        loop = asyncio.get_running_loop()
        schedules_by_date = {}
        for photo_msg in photo_msgs:
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                    tmp_path = tmp.name
                await client.download_media(photo_msg, file=tmp_path)

                def _call_replicate():
                    with open(tmp_path, 'rb') as f:
                        return self.replicate_client.run(
                            'openai/gpt-5-nano',
                            input={
                                'prompt': prompt,
                                'messages': [],
                                'verbosity': 'low',
                                'image_input': [f],
                                'reasoning_effort': 'high',
                            },
                        )

                output = await loop.run_in_executor(None, _call_replicate)
                if isinstance(output, list):
                    text = ''.join(str(part) for part in output)
                else:
                    text = str(output)

                schedule = self._parse_schedule_json(text, tz)
                if schedule and schedule.get('date'):
                    schedules_by_date[str(schedule['date'])] = schedule
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass

        if not schedules_by_date:
            return None
        return list(schedules_by_date.values())


async def dtek_schedule_login():
    global schedule_login_in_progress

    if TelegramClient is None:
        print("Telethon is not installed. Install dependencies and try again.")
        return

    config = load_config()
    settings = config['DEFAULT']

    api_id_raw = (settings.get('DTEK_TELEGRAM_API_ID', '') or '').strip()
    api_hash = (settings.get('DTEK_TELEGRAM_API_HASH', '') or '').strip()
    session_name = (settings.get('DTEK_TELEGRAM_SESSION_NAME', '') or 'dtek_schedule_session').strip()

    try:
        api_id = int(api_id_raw) if api_id_raw else 0
    except Exception:
        api_id = 0

    if not api_id or not api_hash:
        print("DTEK Telegram API credentials are missing. Configure them first in Configuration menu.")
        return

    if not os.path.isabs(session_name):
        session_name = os.path.join(CONFIG_DIR, session_name)

    delete_session = input("Delete existing Telethon session file before login? (y/n): ").strip().lower() == 'y'
    if delete_session:
        for suffix in ('.session', '.session-journal'):
            path = session_name + suffix
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass

        parent = os.path.dirname(session_name)
        base = os.path.basename(session_name)
        try:
            for name in os.listdir(parent):
                if name.startswith(base + '.session-'):
                    try:
                        os.remove(os.path.join(parent, name))
                    except Exception:
                        pass
        except Exception:
            pass

    schedule_login_in_progress = True
    try:
        client = TelegramClient(session_name, api_id, api_hash)
        await client.start()
        try:
            is_auth = await client.is_user_authorized()
        except Exception:
            is_auth = False
        if is_auth:
            print(f"Telethon login successful. Session saved to: {session_name}.session")
        else:
            print("Telethon login did not complete successfully.")
        await client.disconnect()
    finally:
        schedule_login_in_progress = False


def _load_dtek_schedule_cache_from_disk():
    global dtek_schedule_cache, dtek_schedule_last_updated

    try:
        if not os.path.exists(DTEK_SCHEDULE_CACHE_FILE):
            return
        with open(DTEK_SCHEDULE_CACHE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return
        schedules = data.get('schedules')
        if isinstance(schedules, dict):
            dtek_schedule_cache = schedules
        last_updated = data.get('last_updated')
        if isinstance(last_updated, str):
            dtek_schedule_last_updated = last_updated
    except Exception:
        return


def _save_dtek_schedule_cache_to_disk():
    try:
        payload = {
            'last_updated': dtek_schedule_last_updated,
            'schedules': dtek_schedule_cache,
        }
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp_path = None
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, dir=CONFIG_DIR, encoding='utf-8') as tmp:
            tmp_path = tmp.name
            json.dump(payload, tmp, ensure_ascii=False, indent=2)
        os.replace(tmp_path, DTEK_SCHEDULE_CACHE_FILE)
    except Exception:
        try:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception:
            pass


async def show_dtek_schedule_cache():
    config = load_config()
    settings = config['DEFAULT']
    tz = pytz.timezone(settings.get('TIMEZONE', 'UTC'))
    now = datetime.now(tz)
    if not dtek_schedule_cache:
        _load_dtek_schedule_cache_from_disk()
    print(f"DTEK queue: {(settings.get('DTEK_QUEUE', '') or '3.1').strip()}")
    print(f"Last schedule update: {dtek_schedule_last_updated or 'Never'}")
    for day_offset in range(2):
        date_str = (now + timedelta(days=day_offset)).date().strftime('%Y-%m-%d')
        schedule = dtek_schedule_cache.get(date_str)
        label = 'Today' if day_offset == 0 else 'Tomorrow'
        if not schedule:
            print(f"{label} ({date_str}): <no schedule cached>")
        else:
            print(f"{label} ({date_str}):")
            try:
                print(json.dumps(schedule, ensure_ascii=False, indent=2))
            except Exception:
                print(str(schedule))


async def force_fetch_dtek_schedule():
    global dtek_schedule_cache, dtek_schedule_last_updated

    config = load_config()
    settings = config['DEFAULT']

    if TelegramClient is None or replicate is None:
        print("Telethon/Replicate are not installed. Install dependencies and try again.")
        return

    api_id_raw = (settings.get('DTEK_TELEGRAM_API_ID', '') or '').strip()
    api_hash = (settings.get('DTEK_TELEGRAM_API_HASH', '') or '').strip()
    session_name = (settings.get('DTEK_TELEGRAM_SESSION_NAME', '') or 'dtek_schedule_session').strip()
    channel = (settings.get('DTEK_CHANNEL', '') or 'dtek_ua').strip()
    queue = (settings.get('DTEK_QUEUE', '') or '3.1').strip()
    replicate_token = (settings.get('REPLICATE_API_TOKEN', '') or '').strip()
    tz = pytz.timezone(settings.get('TIMEZONE', 'UTC'))

    try:
        api_id = int(api_id_raw) if api_id_raw else 0
    except Exception:
        api_id = 0

    if not api_id or not api_hash or not replicate_token:
        print("DTEK schedule credentials are missing. Configure DTEK Telegram API ID/Hash and Replicate token first.")
        return

    fetcher = DtekScheduleFetcher(api_id, api_hash, session_name, channel, queue, replicate_token)
    try:
        schedules = await fetcher.fetch_latest_schedule(tz)
    except Exception as e:
        print(f"Force fetch failed: {e}")
        return

    now = datetime.now(tz)
    today_str = now.date().strftime('%Y-%m-%d')
    tomorrow_str = (now + timedelta(days=1)).date().strftime('%Y-%m-%d')

    if schedules:
        if isinstance(schedules, dict):
            schedules = [schedules]
        for schedule in schedules:
            if schedule and schedule.get('date'):
                dtek_schedule_cache[str(schedule['date'])] = schedule
        dtek_schedule_cache = {k: v for k, v in dtek_schedule_cache.items() if k in (today_str, tomorrow_str)}
        dtek_schedule_last_updated = datetime.now(tz).isoformat()
        _save_dtek_schedule_cache_to_disk()
        print("Force fetch completed.")
    else:
        print("Force fetch completed but no readable schedule was found in the last message photos.")

    await show_dtek_schedule_cache()


def _format_dtek_schedule_update_html(schedule: dict):
    date_str = str((schedule or {}).get('date') or '').strip()
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        date_pretty = dt.strftime('%d.%m.%Y')
    except Exception:
        date_pretty = date_str

    lines = [
        '<b>ℹ️ Оновлено графік відключення електропостачання</b>',
        '',
        f'<b>{date_pretty}:</b>',
    ]

    periods = (schedule or {}).get('periods', []) or []
    if not periods:
        lines.append('   ✅ без відключень')
        return '\n'.join(lines)

    for item in periods:
        if not (isinstance(item, (list, tuple)) and len(item) == 2):
            continue
        start_str = str(item[0])
        end_str = str(item[1])
        lines.append(f'   🪫 з <b>{start_str}</b> до <b>{end_str}</b>')

    return '\n'.join(lines)


def _dtek_schedules_equal(a: dict, b: dict):
    if not a or not b:
        return False
    return (
        str(a.get('date')) == str(b.get('date'))
        and str(a.get('queue')) == str(b.get('queue'))
        and (a.get('periods') or []) == (b.get('periods') or [])
    )

# Monitor loop
async def monitor():
    global dev_mode
    global simulated_values
    global reset_last_values
    global last_grid_status, last_ve_bus_status
    global last_soc, battery_low_reported, battery_critical_reported
    global last_voltage_phases, power_issue_counters, power_issue_reported, voltage_issue_reported
    global dtek_schedule_cache, dtek_schedule_last_updated
    first_run = True
    tuya_controller = None
    schedule_fetcher = None
    next_schedule_fetch_ts = 0.0
    schedule_fetch_failures = 0
    pre_outage_triggered = set()

    while True:
        try:
            if reset_last_values:
                reset_last_values = False
                last_grid_status = None
                last_ve_bus_status = None
                last_soc = None
                battery_low_reported = False
                battery_critical_reported = False
                last_voltage_phases = {1: None, 2: None, 3: None}
                power_issue_counters = {1: 0, 2: 0, 3: 0}
                power_issue_reported = {1: False, 2: False, 3: False}
                voltage_issue_reported = {1: False, 2: False, 3: False}
                grid_status, ve_bus_status, soc, voltage_phases, output_voltages, output_currents, ve_bus_state = get_status(VICTRON_API_URL, API_KEY)
                last_grid_status = grid_status
                last_ve_bus_status = ve_bus_status
                last_soc = soc

            config = load_config()
            settings = config['DEFAULT']
            messages = load_messages(config)

            battery_low_threshold = float(settings.get('BATTERY_LOW_SOC_THRESHOLD', 20))
            battery_critical_threshold = float(settings.get('BATTERY_CRITICAL_SOC_THRESHOLD', 10))

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
                soc = simulated_values.get('soc', last_soc)
                voltage_phases = simulated_values.get('voltage_phases', last_voltage_phases)
                output_voltages = simulated_values.get('output_voltages', {})
                output_currents = simulated_values.get('output_currents', {})
                ve_bus_state = simulated_values.get('ve_bus_state', None)
                # Simulated timestamp
                timestamp = datetime.now(local_tz).strftime("%d.%m.%Y %H:%M")
            else:
                # Fetch from API
                grid_status, ve_bus_status, soc, voltage_phases, output_voltages, output_currents, ve_bus_state = get_status(VICTRON_API_URL, API_KEY)
                timestamp = datetime.now(local_tz).strftime("%d.%m.%Y %H:%M")

            logging.debug(f"Fetched grid_status: {grid_status}")
            logging.debug(f"Fetched ve_bus_status: {ve_bus_status}")
            logging.debug(f"Fetched SOC: {soc}")
            logging.debug(f"Fetched voltage_phases: {voltage_phases}")
            logging.debug(f"Fetched output_voltages: {output_voltages}")
            logging.debug(f"Fetched output_currents: {output_currents}")
            logging.debug(f"Fetched ve_bus_state: {ve_bus_state}")

            # Skip sending messages on the first run to set the initial states
            if first_run:
                last_grid_status = grid_status
                last_ve_bus_status = ve_bus_status
                last_soc = soc
                if soc <= battery_low_threshold:
                    battery_low_reported = True
                if soc <= battery_critical_threshold:
                    battery_critical_reported = True
                first_run = False
                await asyncio.sleep(REFRESH_PERIOD)
                continue

            # Initialize TuyaController if credentials are available
            tuya_enabled = is_tuya_configured(config)

            if tuya_enabled and tuya_controller is None:
                tuya_device_ids = [id.strip() for id in settings['TUYA_DEVICE_IDS'].split(',')]
                tuya_controller = TuyaController(
                    settings['TUYA_ACCESS_ID'],
                    settings['TUYA_ACCESS_KEY'],
                    settings['TUYA_API_ENDPOINT'],
                    ','.join(tuya_device_ids)  # Pass as comma-separated string
                )
                logging.info("Tuya Controller initialized with device IDs: %s", tuya_device_ids)
            elif not tuya_enabled:
                tuya_controller = None
                logging.warning("Tuya configuration is missing. Device control is disabled.")

            schedule_enabled = bool(settings.get('SCHEDULE_ENABLED', '').strip())
            if schedule_enabled and tuya_controller and not schedule_login_in_progress:
                api_id_raw = (settings.get('DTEK_TELEGRAM_API_ID', '') or '').strip()
                api_hash = (settings.get('DTEK_TELEGRAM_API_HASH', '') or '').strip()
                session_name = (settings.get('DTEK_TELEGRAM_SESSION_NAME', '') or 'dtek_schedule_session').strip()
                channel = (settings.get('DTEK_CHANNEL', '') or 'dtek_ua').strip()
                queue = (settings.get('DTEK_QUEUE', '') or '3.1').strip()
                replicate_token = (settings.get('REPLICATE_API_TOKEN', '') or '').strip()
                try:
                    refresh_minutes = int((settings.get('SCHEDULE_REFRESH_MINUTES', '60') or '60').strip())
                except Exception:
                    refresh_minutes = 60
                try:
                    pre_minutes = int((settings.get('PRE_OUTAGE_TUYA_OFF_MINUTES', '5') or '5').strip())
                except Exception:
                    pre_minutes = 5

                try:
                    api_id = int(api_id_raw) if api_id_raw else 0
                except Exception:
                    api_id = 0

                if TelegramClient is None or replicate is None:
                    schedule_fetcher = None
                elif not api_id or not api_hash or not replicate_token:
                    schedule_fetcher = None
                else:
                    if schedule_fetcher is None:
                        schedule_fetcher = DtekScheduleFetcher(api_id, api_hash, session_name, channel, queue, replicate_token)

                now = datetime.now(local_tz)
                pre_outage_triggered = {k for k in pre_outage_triggered if k > now - timedelta(days=1)}

                if schedule_fetcher is not None:
                    refresh_seconds = max(60, refresh_minutes * 60)
                    now_ts = time.time()
                    if now_ts >= next_schedule_fetch_ts:
                        try:
                            schedules = await schedule_fetcher.fetch_latest_schedule(local_tz)
                            if schedules:
                                if isinstance(schedules, dict):
                                    schedules = [schedules]

                                updated_schedules = []
                                for schedule in schedules:
                                    if schedule and schedule.get('date'):
                                        ds = str(schedule['date'])
                                        prev = dtek_schedule_cache.get(ds)
                                        if prev is None or not _dtek_schedules_equal(prev, schedule):
                                            updated_schedules.append(schedule)
                                        dtek_schedule_cache[ds] = schedule
                                today_str = now.date().strftime('%Y-%m-%d')
                                tomorrow_str = (now + timedelta(days=1)).date().strftime('%Y-%m-%d')
                                dtek_schedule_cache = {k: v for k, v in dtek_schedule_cache.items() if k in (today_str, tomorrow_str)}
                                dtek_schedule_last_updated = datetime.now(local_tz).isoformat()
                                _save_dtek_schedule_cache_to_disk()

                                for sched in updated_schedules:
                                    try:
                                        msg = _format_dtek_schedule_update_html(sched)
                                        await send_telegram_message(bot, CHAT_ID, msg, TIMEZONE, is_test_message=dev_mode, parse_mode='HTML')
                                    except Exception as e:
                                        logging.error(f"Failed to send schedule update message: {e}")
                            schedule_fetch_failures = 0
                            next_schedule_fetch_ts = now_ts + refresh_seconds
                        except Exception as e:
                            schedule_fetch_failures += 1
                            err_str = str(e)
                            if 'key is not registered in the system' in err_str:
                                logging.error("Schedule fetch failed: Telethon session key is not registered. Delete the .session file in ~/victron_monitor/ and log in again interactively.")
                            else:
                                logging.error(f"Schedule fetch failed: {e}")
                            backoff_seconds = min(refresh_seconds, 5 * (2 ** min(schedule_fetch_failures, 6)))
                            next_schedule_fetch_ts = now_ts + backoff_seconds

                if dtek_schedule_cache and grid_status and grid_status[0] == 0:
                    trigger_window_seconds = max(60, REFRESH_PERIOD * 2)
                    for day_offset in range(2):
                        target_date = (now + timedelta(days=day_offset)).date()
                        date_str = target_date.strftime('%Y-%m-%d')
                        schedule = dtek_schedule_cache.get(date_str)
                        if not schedule:
                            continue
                        periods = schedule.get('periods', []) or []
                        for item in periods:
                            if not (isinstance(item, (list, tuple)) and len(item) == 2):
                                continue
                            start_str = str(item[0])
                            try:
                                start_time = _parse_hhmm(start_str)
                            except Exception:
                                continue
                            start_dt_naive = datetime.combine(target_date, start_time)
                            start_dt = local_tz.localize(start_dt_naive)
                            trigger_dt = start_dt - timedelta(minutes=pre_minutes)
                            delta = (now - trigger_dt).total_seconds()
                            if 0 <= delta <= trigger_window_seconds:
                                if start_dt not in pre_outage_triggered:
                                    try:
                                        await tuya_controller.turn_devices_off()
                                        pre_outage_triggered.add(start_dt)
                                        logging.info(f"Tuya devices turned off {pre_minutes} minutes before scheduled outage at {start_dt.isoformat()}")
                                    except Exception as e:
                                        logging.error(f"Error turning off Tuya devices before outage: {e}")

            # Check and send grid status updates
            if grid_status != last_grid_status:
                if grid_status is not None:
                    status_code, status_description = grid_status

                    if status_code == 2:
                        # Grid is down
                        message = messages['GRID_DOWN_MSG'].replace('{timestamp}', timestamp)
                        await send_telegram_message(bot, CHAT_ID, message, TIMEZONE, is_test_message=dev_mode)
                        logging.info(f"Grid status changed to DOWN: {status_description}")

                        # Turn off Tuya devices
                        if tuya_controller:
                            try:
                                await tuya_controller.turn_devices_off()
                                logging.info("Tuya devices turned off due to grid down.")
                            except Exception as e:
                                logging.error(f"Error turning off Tuya devices: {e}")

                    elif status_code == 0:
                        # Grid is restored
                        message = messages['GRID_UP_MSG'].replace('{timestamp}', timestamp)
                        await send_telegram_message(bot, CHAT_ID, message, TIMEZONE, is_test_message=dev_mode)
                        logging.info(f"Grid status changed to RESTORED: {status_description}")

                        # Turn on Tuya devices
                        if tuya_controller:
                            try:
                                await tuya_controller.turn_devices_on()
                                logging.info("Tuya devices turned on due to grid restoration.")
                            except Exception as e:
                                logging.error(f"Error turning on Tuya devices: {e}")
                    else:
                        logging.warning(f"Received unexpected grid_status value: {status_code} ({status_description}). Ignoring.")
                else:
                    logging.debug("Received grid_status is None. No action taken.")

                last_grid_status = grid_status

            if soc is not None and last_soc is not None:
                # Check for low battery
                if last_soc > battery_low_threshold and soc <= battery_low_threshold and not battery_low_reported:
                    message = messages['LOW_BATTERY_MSG'].format(soc=soc, timestamp=timestamp)
                    await send_telegram_message(bot, CHAT_ID, message, TIMEZONE, is_test_message=dev_mode)
                    battery_low_reported = True
                    logging.info(f"Low battery detected: SOC={soc}%")

                # Check for critical battery
                if last_soc > battery_critical_threshold and soc <= battery_critical_threshold and not battery_critical_reported:
                    message = messages['CRITICAL_BATTERY_MSG'].format(soc=soc, timestamp=timestamp)
                    await send_telegram_message(bot, CHAT_ID, message, TIMEZONE, is_test_message=dev_mode)
                    battery_critical_reported = True
                    logging.info(f"Critical battery detected: SOC={soc}%")

                # Reset reports if SOC rises above thresholds
                if soc > battery_low_threshold and battery_low_reported:
                    battery_low_reported = False
                    logging.info(f"SOC recovered above low threshold: SOC={soc}%")
                if soc > battery_critical_threshold and battery_critical_reported:
                    battery_critical_reported = False
                    logging.info(f"SOC recovered above critical threshold: SOC={soc}%")

            last_soc = soc

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
                        nominal_voltage = float(settings['NOMINAL_VOLTAGE'])
                        power = nominal_voltage * output_currents[phase][0]
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

            # Check power consumption on each phase if the VE.Bus state is "Passthru"
            if ve_bus_state == PASSTHRU_STATE:
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

            await asyncio.sleep(REFRESH_PERIOD)
        except Exception as e:
            logging.error(f"Error: {e}")
            await asyncio.sleep(REFRESH_PERIOD)

# Async function to send a message to the Telegram group
async def send_telegram_message(bot, CHAT_ID, message, TIMEZONE, is_test_message=False, parse_mode=None):
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

    await bot.send_message(chat_id=CHAT_ID, text=message, disable_notification=disable_notification, parse_mode=parse_mode)

# Main menu
async def main():
    config = load_config()

    logging_level_str = config['DEFAULT'].get('LOG_LEVEL', 'INFO').upper()
    logging_level = getattr(logging, logging_level_str, logging.INFO)
    logging.getLogger().setLevel(logging_level)

    if not sys.stdin.isatty():
        print("Running in non-interactive mode. Starting monitor.")
        await monitor()
        return

    # Start the monitor function as a background task
    asyncio.create_task(monitor())

    while True:
        config = load_config()
        current_log_level = config['DEFAULT'].get('LOG_LEVEL', 'INFO').upper()
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
        print(f"9. Set Logging Level (Current: {current_log_level})")
        print("10. DTEK Schedule Login (Telethon)")
        print("11. Show DTEK Schedule (cached)")
        print("12. Force Fetch DTEK Schedule")
        print("13. Exit")

        choice = input("Enter your choice (1-13): ")

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
            setup_logging_level()
        elif choice == '10':
            await dtek_schedule_login()
        elif choice == '11':
            await show_dtek_schedule_cache()
        elif choice == '12':
            await force_fetch_dtek_schedule()
        elif choice == '13':
            sys.exit(0)
        else:
            print("Invalid choice. Please try again.")

if __name__ == '__main__':
    asyncio.run(main())