import asyncio
import hashlib
import math
import os
import requests
import sys
import time

from collections import defaultdict

from meross_iot.controller.mixins.electricity import ElectricityMixin
from meross_iot.http_api import MerossHttpClient
from meross_iot.manager import MerossManager

from dotenv import load_dotenv
load_dotenv()

EMAIL = os.environ.get('MEROSS_EMAIL')
PASSWORD = os.environ.get('MEROSS_PASSWORD')
POLL_FREQUENCY_S = float(os.environ.get('MEROSS_POLL_FREQUENCY_S') or 10)

SEMP2REST_ORIGIN = os.environ.get('SEMP2REST_ORIGIN')

POLL_TIMEOUT_S = 1

samples = defaultdict(list)
sample_ts = defaultdict(list)


def device_id_from_uuid(uuid, channel):
    serial = hashlib.md5(uuid.encode('utf-8')).hexdigest()[:12]
    return f"F-11223344-{serial}-{channel:0>2d}"


def set_last_power(device_id):
    payload = {
        "power": {
            "Watts": sum(samples[device_id])/len(samples[device_id]),
            "MinPower": min(samples[device_id]),
            "MaxPower": max(samples[device_id]),
        },
    }
    r = requests.put(f"{SEMP2REST_ORIGIN}/api/devices/{device_id}/lastPower", data=payload)
    r.raise_for_status()


def register_device(dev):
    payload = {
        "device": {
           "deviceId": device_id_from_uuid(dev.uuid, 0),
           "name": dev.name,
           "type": "Other", # AirConditioning, Charger, DishWasher, Dryer, ElectricVehicle, EVCharger, Freezer, Fridge, Heater, HeatPump, Motor, Pump, WashingMachine, Other
           "measurementMethod": "Measurement",  # Measurement, Estimation, None
           "interruptionsAllowed": False,
           "maxPower": 3680,  # 230V 16A
           "emSignalsAccepted": False,
           "status": "On",  # On, Off, Offline
           "vendor": Meross,
           "serialNr": dev.uuid,
           "absoluteTimestamps": False,  # we don't accept scheduling events either way
           "optionalEnergy": False,
        },
    }
    r = requests.post(f"{SEMP2REST_ORIGIN}/api/devices", data=payload)
    r.raise_for_status()


async def main():
    # Setup the HTTP client API from user-password
    http_api_client = await MerossHttpClient.async_from_user_password(email=EMAIL, password=PASSWORD)

    # Setup and start the device manager
    manager = MerossManager(http_client=http_api_client)
    try:
        await manager.async_init()

        # Retrieve all the MSS310 devices that are registered on this account
        await manager.async_device_discovery()
        devs = manager.find_devices(device_class=ElectricityMixin)

        while True:
            start = time.time()
            for dev in devs:
                device_id = device_id_from_uuid(dev.uuid, 0)

                while len(sample_ts[device_id]) > 0 and sample_ts[device_id][0] < start - 60:
                    samples[device_id].pop(0)
                    sample_ts[device_id].pop(0)
                
                # TODO: might want to fetch these in parallel...
                result = await dev.async_get_instant_metrics(timeout=POLL_TIMEOUT_S)

                if result:
                    samples[device_id].append(result.power)
                    sample_ts[device_id].append(result.sample_timestamp)

                    # try updating lastPower, assuming device exists. Create the device if we get a 404 response
                    try:
                        set_last_power(device_id)
                    except requests.exceptions.HTTPError as e:
                        if e.response.status_code == 404:
                            register_device(dev)
                        else:
                            print(e)
                    except Exception as e:
                        # just print and struggle on
                        print(e)

            await asyncio.sleep(POLL_FREQUENCY_S - (time.time() - start))

    finally:
        # Close the manager and logout from http_api
        manager.close()
        await http_api_client.async_logout()


if __name__ == '__main__':
    # Windows and python 3.8 requires to set up a specific event_loop_policy.
    #  On Linux and MacOSX this is not necessary.
    if not EMAIL or not PASSWORD:
        print("Environment variables MEROSS_EMAIL or MEROSS_PASSWORD not set")
        sys.exit(1)
    if not SEMP2REST_ORIGIN:
        print("Environment variable SEMP2REST_ORIGIN not set")
        sys.exit(1)

    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
    loop.close()
