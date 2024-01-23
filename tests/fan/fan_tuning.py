#!/usr/bin/env python3
import json
import time
import threading
import logging
from typing import Tuple, List

from panda import Panda

# Constants
SLEEP_INTERVAL = 0.1
POWER_INCREMENT = 10
MAX_MEASURE_DURATION = 70  # seconds
FAN_STOP_RPM_THRESHOLD = 100
EXPECTED_RPM_TOLERANCE = 0.1  # 10%

# Initialize Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def drain_serial(p: Panda) -> List[str]:
    """Reads all available data from the serial interface."""
    ret = []
    while True:
        data = p.serial_read(0)
        if len(data) == 0:
            break
        ret.append(data.decode('utf-8').strip())
    return ret

def log_fan_data(p: Panda, fan_cmd: float, file_path: str, event: threading.Event):
    """Logs fan data to a file."""
    t = time.monotonic()
    drain_serial(p)

    while not event.is_set():
        p.set_fan_power(fan_cmd)
        latest_log = next((l for l in reversed(drain_serial(p)) if len(l.split(' ')) == 4), None)
        if latest_log:
            target_rpm, rpm_fast, power, stall_count = map(lambda n: int(n, 16), latest_log.split(' '))
            data = {
                'time_elapsed': time.monotonic() - t,
                'cmd_power': fan_cmd,
                'pwm_power': power,
                'target_rpm': target_rpm,
                'rpm_fast': rpm_fast,
                'rpm': p.get_fan_rpm(),
                'stall_counter': stall_count,
                'total_stall_count': p.health().get('fan_stall_count', 0),
            }
            with open(file_path, 'a') as f:
                f.write(json.dumps(data) + '\n')
        time.sleep(SLEEP_INTERVAL)
    p.set_fan_power(0)

def get_overshoot_rpm(p: Panda, power: float) -> Tuple[float, int, int]:
    """Calculates the overshoot RPM for a given power setting."""
    stop_fan(p)
    time.sleep(3)  # Allow the fan to completely stop
    return measure_fan_overshoot(p, power)

def stop_fan(p: Panda):
    """Stops the fan."""
    while p.get_fan_rpm() > FAN_STOP_RPM_THRESHOLD:
        p.set_fan_power(0)
        time.sleep(SLEEP_INTERVAL)

def measure_fan_overshoot(p: Panda, power: float) -> Tuple[float, int, int]:
    """Measures the maximum RPM and power overshoot for a given fan power."""
    p.set_fan_power(power)
    max_rpm = max_power = 0
    for _ in range(MAX_MEASURE_DURATION):
        rpm = p.get_fan_rpm()
        max_rpm = max(max_rpm, rpm)
        max_power = max(max_power, p.health().get('fan_power', 0))
        time.sleep(SLEEP_INTERVAL)

    expected_rpm = Panda.MAX_FAN_RPMs[bytes(p.get_type())] * power / 100
    overshoot = (max_rpm / expected_rpm) - 1
    return overshoot, max_rpm, max_power

def start_logger_thread(p: Panda, fan_cmd: float, file_path: str) -> threading.Event:
    """Starts a logger thread."""
    event = threading.Event()
    threading.Thread(target=log_fan_data, args=(p, fan_cmd, file_path, event)).start()
    return event

if __name__ == "__main__":
    file_path = '/tmp/fan_log'
    fan_cmd = 0.0  # Default fan power

    try:
        with Panda() as p:
            event = start_logger_thread(p, fan_cmd, file_path)
            for power in range(POWER_INCREMENT, 101, POWER_INCREMENT):
                overshoot, max_rpm, max_power = get_overshoot_rpm(p, power)
                logger.info(f"Fan power {power}%: overshoot {overshoot:.2%}, Max RPM {max_rpm}, Max power {max_power}%")
    except Exception as e:
        logger.error(f"An error occurred: {e}")
    finally:
        event.set()
