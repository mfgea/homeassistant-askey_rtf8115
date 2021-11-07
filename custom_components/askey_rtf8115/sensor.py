"""
Support for reading Askey RTF8115 Router (Movistar Argentina) status.

configuration.yaml

sensor:
    - platform: askey_rtf8115
        host: IP_ADDRESS
        scan_interval: 60
        resources:
            - model
            - description
            - serial
            - gponsn
            - hwversion
            - swversion
            - connected
            - cpu1m
            - cpu5m
            - cpu15m
            - memtotal
            - memused
            - memfree
            - mac
"""
import logging
import re
import html
from datetime import timedelta
import aiohttp
import asyncio
import async_timeout
import voluptuous as vol

from homeassistant.components.sensor import PLATFORM_SCHEMA
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.const import CONF_HOST, CONF_RESOURCES
from homeassistant.util import Throttle
from homeassistant.helpers.entity import Entity

DOMAIN = "askey_rtf8115"

BASE_URL = "http://{0}/te_info.asp"
_LOGGER = logging.getLogger(__name__)

MIN_TIME_BETWEEN_UPDATES = timedelta(seconds=60)

SENSOR_PREFIX = "RTF8115 "
SENSOR_TYPES = {
    "model": ["Model", "", "mdi:router-wireless"],
    "description": ["Description", "", "mdi:router-wireless"],
    "serial": ["Serial", "", "mdi:router-wireless"],
    "gponsn": ["GPON SN", "", "mdi:router-wireless"],
    "hwversion": ["Hardware Version", "", "mdi:file-document"],
    "swversion": ["Software Version", "", "mdi:file-document"],
    "state": ["State", "", "mdi:power-plug"],
    "cpu1m": ["Load Avg 1m ", "", "mdi:lock-outline"],
    "cpu5m": ["Load Avg 5m", "", "mdi:cog"],
    "cpu15m": ["Load Avg 15m", "", "mdi:map-marker-radius"],
    "memtotal": ["Mem. Total", "kb", "mdi:radio-tower"],
    "memused": ["Mem. Used", "kb", "mdi:forum-outline"],
    "memfree": ["Mem. Free", "kb", "mdi:gauge"],
    "mac": ["MAC Address", "", "mdi:gauge"],
    "voltage": ["Voltage", "V", "mdi:gauge"],
    "temp": ["Temperature", "C", "mdi:gauge"],
    "opticaltx": ["Optical TX", "dBm", "mdi:gauge"],
    "opticalrx": ["Optical RX", "dBm", "mdi:gauge"],
}

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_RESOURCES, default=list(SENSOR_TYPES)): vol.All(
            cv.ensure_list, [vol.In(SENSOR_TYPES)]
        ),
    }
)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Setup the Askey RTF8115 sensors."""

    # scan_interval = config.get(CONF_SCAN_INTERVAL)
    host = config.get(CONF_HOST)

    askeydata = AskeyRtf8115Data(hass, host)
    await askeydata.async_update()

    entities = []
    for resource in config[CONF_RESOURCES]:
        sensor_type = resource.lower()
        name = SENSOR_PREFIX + SENSOR_TYPES[resource][0]
        unit = SENSOR_TYPES[resource][1]
        icon = SENSOR_TYPES[resource][2]

        _LOGGER.debug(
            "Adding Askey RTF8115 sensor: %s, %s, %s, %s",
            name,
            sensor_type,
            unit,
            icon,
        )
        entities.append(AskeyRtf8115Sensor(askeydata, name, sensor_type, unit, icon))

    async_add_entities(entities, True)


# pylint: disable=abstract-method
class AskeyRtf8115Data(object):
    """Handle Askey RTF8115 object and limit updates."""

    def __init__(self, hass, host):
        """Initialize the data."""
        self._hass = hass
        self._host = host

        self._url = BASE_URL.format(self._host)
        self._data = None

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    async def async_update(self):
        """Update the data from the Askey RTF8115."""
        _LOGGER.debug("Downloading data from Askey RTF8115: %s", self._url)

        try:
            websession = async_get_clientsession(self._hass)
            with async_timeout.timeout(10):
                response = await websession.get(self._url)
            _LOGGER.debug("Response status from Askey RTF8115: %s", response.status)
        except (asyncio.TimeoutError, aiohttp.ClientError) as err:
            _LOGGER.error("Cannot connect to Askey RTF8115: %s", err)
            self._data = None
            return
        except Exception as err:
            _LOGGER.error("Error downloading from Askey RTF8115: %s", err)
            self._data = None
            return

        try:
            response_body = await response.text()
            js_content = re.search(
                r"<!-- hide(.*)done hiding -->",
                response_body,
                re.MULTILINE | re.DOTALL,
            ).group(1)
            self._data = dict()

            self._data["model"] = self.parse_simple_value(js_content, "tdModel")
            self._data["description"] = html.unescape(
                self.parse_simple_value(js_content, "tdDesc")
            )
            self._data["serial"] = self.parse_simple_value(js_content, "tdSn")
            self._data["hwversion"] = self.parse_simple_value(js_content, "tdHw")
            self._data["swversion"] = self.parse_simple_value(js_content, "tdSw")
            self._data["mac"] = self.parse_simple_value(js_content, "tdMac")
            self._data["state"] = self.parse_simple_value(js_content, "tdOpt")
            self._data["gponsn"] = self.parse_gpon_value(js_content)

            cpu = self.parse_cpu_values(js_content)
            self._data["cpu1m"] = cpu["cpu1m"]
            self._data["cpu5m"] = cpu["cpu5m"]
            self._data["cpu15m"] = cpu["cpu15m"]

            mem = self.parse_mem_values(js_content)
            self._data["memtotal"] = mem["total"]
            self._data["memused"] = mem["used"]
            self._data["memfree"] = mem["free"]

            pwr_temp = self.parse_power_temp_values(js_content)
            self._data["voltage"] = pwr_temp["voltage"]
            self._data["temp"] = pwr_temp["temp"]

            tx_rx = self.parse_tx_rx_values(js_content)
            self._data["opticaltx"] = tx_rx["opt_tx"]
            self._data["opticalrx"] = tx_rx["opt_rx"]

            _LOGGER.debug("Data received from Askey RTF8115: %s", self._data)
        except Exception as err:
            _LOGGER.error(
                "Cannot parse data from Askey RTF8115: %s -- %s", err, js_content
            )
            self._data = None
            return

    def parse_simple_value(self, js_content, name):
        """Parse a simple value from the js code"""
        matches = re.search(r"\$\('#" + name + r"'\).html\('(.*)'\);", js_content)
        return matches.group(1)

    def parse_gpon_value(self, js_content):
        """Parse GPON value from the js code"""

        matches = re.search(r"var gponSn = '([\w\d]+)'", js_content)
        gpon_sn = matches.group(1)
        hex_gpon = re.sub(
            r"(.)", lambda m: format(ord(m.group(1)), "x") + "-", gpon_sn[0:4]
        ) + re.sub(r"(..)", "\\1-", gpon_sn[4:])

        return hex_gpon[0:-1]

    def parse_cpu_values(self, js_content):
        """Parse CPU values (loadavg) from the js code"""

        line = self.parse_simple_value(js_content, "tdCpu")
        matches = re.search(r"average: ([\d\.]+), ([\d\.]+), ([\d\.]+)", line)
        ret = dict()
        ret["cpu1m"] = matches.group(1)
        ret["cpu5m"] = matches.group(2)
        ret["cpu15m"] = matches.group(3)
        return ret

    def parse_mem_values(self, js_content):
        """Parse mem values from the js code"""

        line = self.parse_simple_value(js_content, "tdMem")
        matches = re.search(r"total:(\d+), used:(\d+), free:(\d+)", line)
        ret = dict()
        ret["total"] = matches.group(1)
        ret["used"] = matches.group(2)
        ret["free"] = matches.group(3)
        return ret

    def parse_power_temp_values(self, js_content):
        """Parse voltage and temp values from the js code"""

        matches = re.search(r"VOLT:([\d\.]+);TEMP:([\d\.]+)", js_content)
        ret = dict()
        ret["voltage"] = matches.group(1)
        ret["temp"] = matches.group(2)
        return ret

    def parse_tx_rx_values(self, js_content):
        """Parse optical tx and rx values from the js code"""

        matches = re.search(r"TX:([\d\.-]+) dBm;RX:([\d\.-]+) dBm", js_content)
        ret = dict()
        ret["opt_tx"] = matches.group(1)
        ret["opt_rx"] = matches.group(2)
        return ret

    @property
    def latest_data(self):
        """Return the latest data object."""
        if self._data:
            return self._data
        return None


class AskeyRtf8115Sensor(Entity):
    """Representation of Askey RTF8115 data."""

    def __init__(self, askeydata, name, sensor_type, unit, icon):
        """Initialize the sensor."""
        self._askeydata = askeydata
        self._name = name
        self._type = sensor_type
        self._unit = unit
        self._icon = icon

        self._state = None

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def icon(self):
        """Icon to use in the frontend, if any."""
        return self._icon

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement of this entity, if any."""
        return self._unit

    @property
    def device_state_attributes(self):
        """Return the state attributes of this device."""
        attr = {}
        return attr

    async def async_update(self):
        """Get the latest data and use it to update our sensor state."""

        await self._askeydata.async_update()
        askeystatus = self._askeydata.latest_data

        if askeystatus and self._type in askeystatus:
            self._state = askeystatus[self._type]

            _LOGGER.debug("Device: %s State: %s", self._type, self._state)
