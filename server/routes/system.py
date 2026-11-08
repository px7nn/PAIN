# This script is AI-written, has not been verified, and is therefore flagged for verification.


import glob
import time

from pathlib import Path

import psutil
from fastapi import APIRouter

router = APIRouter(prefix="/api/system", tags=["System"])

_previous_network = None
_previous_time = None


def read_int(path):
    try:
        return int(Path(path).read_text().strip())
    except (OSError, ValueError):
        return None


def get_gpu_info():
    devices = []

    for card in glob.glob("/sys/class/drm/card[0-9]*"):
        device = Path(card) / "device"

        vendor = None

        try:
            vendor = (device / "vendor").read_text().strip()
        except OSError:
            continue

        # AMD GPU vendor ID.
        if vendor.lower() != "0x1002":
            continue

        busy = read_int(device / "gpu_busy_percent")
        vram_used = read_int(device / "mem_info_vram_used")
        vram_total = read_int(device / "mem_info_vram_total")
        gtt_used = read_int(device / "mem_info_gtt_used")
        gtt_total = read_int(device / "mem_info_gtt_total")

        temperature = None

        for sensor in glob.glob(str(device / "hwmon" / "hwmon*" / "temp1_input")):
            temperature = read_int(sensor)

            if temperature is not None:
                temperature /= 1000
                break

        devices.append({
            "name": "AMD GPU",
            "vendor": "AMD",
            "utilization_percent": busy,
            "temperature_c": temperature,
            "vram_used_bytes": vram_used,
            "vram_total_bytes": vram_total,
            "gtt_used_bytes": gtt_used,
            "gtt_total_bytes": gtt_total
        })

    return devices


def get_network_info():
    global _previous_network, _previous_time

    current = psutil.net_io_counters()
    now = time.monotonic()

    upload_rate = 0
    download_rate = 0

    if _previous_network is not None and _previous_time is not None:
        elapsed = now - _previous_time

        if elapsed > 0:
            upload_rate = (
                current.bytes_sent - _previous_network.bytes_sent
            ) / elapsed

            download_rate = (
                current.bytes_recv - _previous_network.bytes_recv
            ) / elapsed

    _previous_network = current
    _previous_time = now

    return {
        "upload_bytes_per_sec": max(0, upload_rate),
        "download_bytes_per_sec": max(0, download_rate),
        "total_sent_bytes": current.bytes_sent,
        "total_received_bytes": current.bytes_recv
    }


@router.get("")
def system_metrics():

    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = psutil.disk_usage("/")

    temperatures = {}

    try:
        for name, sensors in psutil.sensors_temperatures().items():
            if sensors:
                temperatures[name] = [
                    {
                        "label": sensor.label or name,
                        "current": sensor.current
                    }
                    for sensor in sensors
                    if sensor.current is not None
                ]
    except (AttributeError, OSError):
        pass

    return {
        "cpu": {
            "usage_percent": psutil.cpu_percent(interval=None),
            "core_count": psutil.cpu_count(logical=True),
            "physical_cores": psutil.cpu_count(logical=False),
            "frequency_mhz": (
                psutil.cpu_freq().current
                if psutil.cpu_freq()
                else None
            ),
            "temperatures": temperatures
        },

        "memory": {
            "total_bytes": memory.total,
            "used_bytes": memory.used,
            "available_bytes": memory.available,
            "usage_percent": memory.percent
        },

        "swap": {
            "total_bytes": swap.total,
            "used_bytes": swap.used,
            "usage_percent": swap.percent
        },

        "disk": {
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
            "usage_percent": disk.percent
        },

        "gpu": get_gpu_info(),

        "network": get_network_info(),

        "timestamp": time.time()
    }