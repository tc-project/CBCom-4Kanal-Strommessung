#!/usr/bin/env python3

import time
import threading
from datetime import datetime, timezone

from smbus2 import SMBus
from flask import Flask, jsonify


I2C_BUS = 1

CHANNELS = {
    1: 0x40,
    2: 0x41,
    3: 0x42,
    4: 0x43,
}

R_SHUNT = 0.050          # 50 mOhm
CURRENT_LSB = 50e-6      # 50 uA/bit
CALIBRATION = 2048

# INA226 configuration:
#
# AVG    = 16 samples
# VBUSCT = 1.1 ms
# VSHCT  = 1.1 ms
# MODE   = continuous shunt + bus
#
# 0x0527
CONFIG = 0x0527

REG_CONFIG = 0x00
REG_SHUNT = 0x01
REG_BUS = 0x02
REG_POWER = 0x03
REG_CURRENT = 0x04
REG_CALIBRATION = 0x05


class INA226:

    def __init__(self, bus, address):
        self.bus = bus
        self.address = address

    def write_register(self, register, value):
        self.bus.write_i2c_block_data(
            self.address,
            register,
            [(value >> 8) & 0xFF, value & 0xFF]
        )

    def read_register(self, register):
        data = self.bus.read_i2c_block_data(
            self.address,
            register,
            2
        )

        return (data[0] << 8) | data[1]

    def read_signed_register(self, register):
        value = self.read_register(register)

        if value & 0x8000:
            value -= 0x10000

        return value

    def initialize(self):
        self.write_register(REG_CONFIG, CONFIG)
        self.write_register(REG_CALIBRATION, CALIBRATION)

        time.sleep(0.01)

    def read_measurement(self):

        shunt_raw = self.read_signed_register(REG_SHUNT)
        bus_raw = self.read_register(REG_BUS)
        current_raw = self.read_signed_register(REG_CURRENT)
        power_raw = self.read_register(REG_POWER)

        shunt_voltage = shunt_raw * 2.5e-6
        bus_voltage = bus_raw * 1.25e-3

        current = current_raw * CURRENT_LSB
        power = power_raw * (25 * CURRENT_LSB)

        return {
            "voltage": bus_voltage,
            "current": current,
            "power": power,
            "shunt_voltage": shunt_voltage,
        }


bus = SMBus(I2C_BUS)

sensors = {}

for channel, address in CHANNELS.items():
    sensor = INA226(bus, address)
    sensor.initialize()
    sensors[channel] = sensor


latest = {
    channel: {
        "voltage": 0.0,
        "current": 0.0,
        "power": 0.0,
        "energy_wh": 0.0,
    }
    for channel in CHANNELS
}

latest_lock = threading.Lock()


def measurement_thread():

    previous_time = time.monotonic()

    while True:

        now = time.monotonic()
        dt = now - previous_time
        previous_time = now

        new_values = {}

        for channel, sensor in sensors.items():

            try:
                measurement = sensor.read_measurement()

                new_values[channel] = measurement

            except Exception as e:
                print(f"Fehler Kanal {channel}: {e}")

        with latest_lock:

            for channel, measurement in new_values.items():

                old_energy = latest[channel]["energy_wh"]

                power = measurement["power"]

                energy_increment = power * dt / 3600.0

                latest[channel] = {
                    "voltage": measurement["voltage"],
                    "current": measurement["current"],
                    "power": power,
                    "shunt_voltage": measurement["shunt_voltage"],
                    "energy_wh": old_energy + energy_increment,
                }

        time.sleep(1.0)


app = Flask(__name__)


@app.route("/api/measurements")
def measurements():

    with latest_lock:
        data = {
            str(channel): values.copy()
            for channel, values in latest.items()
        }

    total_power = sum(
        values["power"]
        for values in data.values()
    )

    total_energy = sum(
        values["energy_wh"]
        for values in data.values()
    )

    return jsonify({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "channels": data,
        "total_power": total_power,
        "total_energy_wh": total_energy,
    })


@app.route("/api/reset")
def reset_energy():

    with latest_lock:

        for channel in latest:
            latest[channel]["energy_wh"] = 0.0

    return jsonify({"status": "ok"})


if __name__ == "__main__":

    thread = threading.Thread(
        target=measurement_thread,
        daemon=True
    )

    thread.start()

    print("Power monitor gestartet.")
    print("http://0.0.0.0:5000/api/measurements")

    app.run(
        host="0.0.0.0",
        port=5000,
        threaded=True
    )
