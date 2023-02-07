import asyncio
import hashlib
import json
import math
import os
import requests
import sys
import time

from collections import defaultdict
from datetime import datetime
from typing import Dict, List

# Configure logging before initializing meross iot so we get timestamps
import logging
logging.basicConfig(
    format='%(asctime)s %(levelname)-8s %(message)s',
    level=logging.INFO,
    datefmt='%Y-%m-%d %H:%M:%S')

from meross_iot.controller.device import BaseDevice
from meross_iot.controller.mixins.electricity import ElectricityMixin
from meross_iot.http_api import MerossHttpClient
from meross_iot.manager import MerossManager
from meross_iot.model.enums import OnlineStatus
from meross_iot.model.plugin.power import PowerInfo
from meross_iot.model.push.generic import GenericPushNotification
from meross_iot.model.push.online import OnlinePushNotification

from dotenv import load_dotenv
load_dotenv()

EMAIL = os.environ.get('MEROSS_EMAIL')
PASSWORD = os.environ.get('MEROSS_PASSWORD')
POLL_FREQUENCY_S = float(os.environ.get('MEROSS_POLL_FREQUENCY_S') or 10)
RECONNECT_TIME_S = float(os.environ.get('MEROSS_RECONNECT_TIME_S') or 30)
STATUS_PRINT_FREQ_S = float(os.environ.get('MEROSS_STATUS_PRINT_FREQ_S') or 3600)
DISCOVER_FREQ_S = float(os.environ.get('MEROSS_DISCOVER_FREQ_S') or 3600)

SEMP2REST_ORIGIN = os.environ.get('SEMP2REST_ORIGIN')

POLL_TIMEOUT_S = 1

class MerossSMA(object):
    samples_by_dev_id: Dict[str, List[float]] = defaultdict(list)
    sample_ts_by_dev_id: Dict[str, List[float]] = defaultdict(list)

    connection_status_by_dev_id: Dict[str, OnlineStatus] = defaultdict(lambda: OnlineStatus.ONLINE)
    disconnection_detected = False

    def device_id_from_uuid(self, uuid: str, channel: int) -> str:
        serial = hashlib.md5(uuid.encode('utf-8')).hexdigest()[:12]
        return f"F-11223344-{serial}-{channel:0>2d}"


    def set_last_power(self, device_id: str):
        payload = {
            "power": {
                "Watts": sum(self.samples_by_dev_id[device_id])/len(self.samples_by_dev_id[device_id]),
                "MinPower": min(self.samples_by_dev_id[device_id]),
                "MaxPower": max(self.samples_by_dev_id[device_id]),
            },
        }
        r = requests.put(f"{SEMP2REST_ORIGIN}/api/devices/{device_id}/lastPower", data=json.dumps(payload), headers={"Content-Type":"application/json"})
        r.raise_for_status()


    def register_device(self, dev: BaseDevice):
        payload = {
            "device": {
               "deviceId": self.device_id_from_uuid(dev.uuid, 0),
               "name": dev.name,
               "type": "Other", # AirConditioning, Charger, DishWasher, Dryer, ElectricVehicle, EVCharger, Freezer, Fridge, Heater, HeatPump, Motor, Pump, WashingMachine, Other
               "measurementMethod": "Measurement",  # Measurement, Estimation, None
               "interruptionsAllowed": False,
               "maxPower": 3680,  # 230V 16A
               "emSignalsAccepted": False,
               "status": "On",  # On, Off, Offline
               "vendor": "Meross",
               "serialNr": dev.uuid,
               "absoluteTimestamps": False,  # we don't accept scheduling events either way
               "optionalEnergy": False,
            },
        }
        r = requests.post(f"{SEMP2REST_ORIGIN}/api/devices", data=json.dumps(payload), headers={"Content-Type":"application/json"})
        if r.status_code != 200:
            logging.warning(f"Status {r.status_code} when creating device: {r.text}")

    async def handle_push_notification(self, notification: GenericPushNotification, devices: List[BaseDevice], manager: MerossManager):
        if isinstance(notification, OnlinePushNotification):
            dev_id = self.device_id_from_uuid(notification.originating_device_uuid, 0)
            self.connection_status_by_dev_id[dev_id] = notification.status
            logging.info(f"Push notification: Device {dev_id} online status changed to {OnlineStatus(notification.status).name}")

            for status in self.connection_status_by_dev_id.values():
                if status != -1:
                    break
            else:
                logging.warning("All devices are in unknown status; disconnection event detected.")
                self.disconnection_detected = True


    async def fetch_and_update_device(self, dev) -> PowerInfo:
        device_id = self.device_id_from_uuid(dev.uuid, 0)

        while len(self.sample_ts_by_dev_id[device_id]) > 0 and self.sample_ts_by_dev_id[device_id][0] < time.time() - 60:
            self.samples_by_dev_id[device_id].pop(0)
            self.sample_ts_by_dev_id[device_id].pop(0)

        try:
            result = await dev.async_get_instant_metrics(timeout=POLL_TIMEOUT_S)
        except Exception:
            return None

        if result:
            self.samples_by_dev_id[device_id].append(result.power)
            self.sample_ts_by_dev_id[device_id].append(result.sample_timestamp.timestamp())

            # try updating lastPower, assuming device exists. Create the device if we get a 404 response
            try:
                self.set_last_power(device_id)
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 404:
                    self.register_device(dev)
                else:
                    logging.warning(e)
            except Exception as e:
                # just print and struggle on
                logging.warning(e)

        return result

    async def connect_and_forward(self) -> None:
        """
        Connects MerossIot and forwards readings via SEMP2REST.
        Exits after no new measurement has been received for RECONNECT_TIME_S seconds
        """
        last_measurement = time.time()
        last_status_print = 0.0
        last_device_update = 0.0
        devs = []

        http_api_client = await MerossHttpClient.async_from_user_password(email=EMAIL, password=PASSWORD)
        manager = MerossManager(http_client=http_api_client)
        try:
            manager.register_push_notification_handler_coroutine(self.handle_push_notification)

            connection_status_by_dev_id = defaultdict(lambda: OnlineStatus.ONLINE)  # type: Dict[str, OnlineStatus]
            disconnection_detected = False
            logging.info("Entering main loop...")

            while (time.time() - last_measurement < RECONNECT_TIME_S) and (not self.disconnection_detected):
                start = time.time()

                # periodically retrieve devices registered on this account
                if time.time() - last_device_update > DISCOVER_FREQ_S:
                    await manager.async_device_discovery()
                    devs = manager.find_devices(device_class=ElectricityMixin)
                    last_device_update = time.time()

                for dev in devs:
                    # TODO: might want to fetch these in parallel...
                    result = await self.fetch_and_update_device(dev)
                    if result:
                        last_measurement = time.time()

                # periodically print stats
                if STATUS_PRINT_FREQ_S > 0 and time.time() - last_status_print > STATUS_PRINT_FREQ_S:
                    logging.info("Device status")
                    for dev in devs:
                        device_id = self.device_id_from_uuid(dev.uuid, 0)
                        avg_watt = sum(self.samples_by_dev_id[device_id])/len(self.samples_by_dev_id[device_id])
                        logging.info(f"  {dev.name} ({device_id}): {avg_watt:7.2f} W")
                    last_status_print = time.time()

                await asyncio.sleep(POLL_FREQUENCY_S - (time.time() - start))

        finally:
            # Close the manager and logout from http_api
            logging.info("Exiting main loop...")
            manager.close()
            await http_api_client.async_logout()


async def main():
    forwarder = MerossSMA()
    while True:
        try:
            await forwarder.connect_and_forward()
        except Exception as e:
            # just print and struggle on
            logging.warning(e)

if __name__ == '__main__':
    if not EMAIL or not PASSWORD:
        print("Environment variables MEROSS_EMAIL or MEROSS_PASSWORD not set")
        sys.exit(1)
    if not SEMP2REST_ORIGIN:
        print("Environment variable SEMP2REST_ORIGIN not set")
        sys.exit(1)

    asyncio.run(main())
