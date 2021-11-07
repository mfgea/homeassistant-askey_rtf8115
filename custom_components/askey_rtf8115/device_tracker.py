"""Support for Askey RTF8115 Router (Movistar Argentina)"""
import logging
import re
import voluptuous as vol

from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.components.device_tracker import (
    DOMAIN,
    PLATFORM_SCHEMA,
    DeviceScanner,
)
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
)

BASE_URL = "http://{0}:8000/"
_LOGGER = logging.getLogger(__name__)

# MIN_TIME_BETWEEN_UPDATES = timedelta(seconds=60)

_LOGGER = logging.getLogger(__name__)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_USERNAME, default="admin"): cv.string,
        vol.Optional(CONF_PASSWORD, default="admin"): cv.string,
    }
)


def mess_userpass(user_pass: str):
    """Encodes (very insecure encoding) username or password to be sent to the router"""
    return re.sub(r"(.)", lambda m: chr(ord(m.group(1)) ^ 0x1F), user_pass)


def get_scanner(hass, config):
    """Validate the configuration and return a scanner."""

    scanner = AskeyDeviceScanner(hass, config[DOMAIN])
    return scanner if scanner.success_init else None


class AskeyDeviceScanner(DeviceScanner):
    """This class queries a Askey RTF8115 Router (Movistar Argentina)"""

    def __init__(self, hass, config):
        """Initialize the scanner."""
        self._hass = hass
        self.host = config[CONF_HOST]
        self.username = config[CONF_USERNAME]
        self.password = config.get(CONF_PASSWORD)

        self.parse_macs = re.compile(
            r"([0-9a-fA-F]{2}:"
            + "[0-9a-fA-F]{2}:"
            + "[0-9a-fA-F]{2}:"
            + "[0-9a-fA-F]{2}:"
            + "[0-9a-fA-F]{2}:"
            + "[0-9a-fA-F]{2})"
        )
        self.parse_device_data = re.compile(r"var deviceData=([\w\W]+?);")

        self.home_url = "http://{ip}/te_info.asp".format(**{"ip": self.host})
        self.login_url = "http://{ip}/cgi-bin/te_acceso_router.cgi".format(
            **{"ip": self.host}
        )
        self.networkmap_url = "http://{ip}/te_mapa_red_local.asp".format(
            **{"ip": self.host}
        )

        self.last_results = {}
        self.success_init = True

    async def async_scan_devices(self):
        """Scan for new devices and return a list with found device IDs."""
        await self._async_update_info()
        return self.last_results

    async def async_get_device_name(self, device):
        """This router doesn't save the name of the wireless device."""
        return None

    async def _async_update_info(self):
        """Ensure the information from the router is up to date.
        Return boolean if scanning successful.
        """
        _LOGGER.info("Checking Router")

        data = await self.get_askey_info()
        if not data:
            return False

        self.last_results = data
        return True

    async def get_askey_info(self):
        """Retrieve data from router."""

        # headers = {}
        payload = {
            "loginUsername": mess_userpass(self.username),
            "loginPassword": mess_userpass(self.password),
        }

        websession = async_get_clientsession(self._hass)

        # Fetch session cookie
        resp = await websession.get(self.home_url)

        _LOGGER.debug("Cookies: %s", resp.cookies)
        websession.cookie_jar.update_cookies(resp.cookies)

        login_response = await websession.post(self.login_url, data=payload)

        result = list()
        if login_response.status == 200:
            _LOGGER.debug("Login request: %s", payload)
            _LOGGER.debug("Login response: %s", await login_response.text())
            _LOGGER.debug("Login cookies: %s", list(websession.cookie_jar))

            items_response = await websession.get(self.networkmap_url)
            _LOGGER.debug("Items response: %s", await items_response.text())
            response_string = await items_response.text()
            devices_data = []
            for line in response_string.split("\n"):
                if "deviceData" in line:
                    line_replaced = line.replace("\\", "")
                    devices_data = eval(
                        self.parse_device_data.search(line_replaced).group(1)
                    )
                    break
            for device in devices_data:
                if device[0] == "1":
                    result.append(device[6])
        else:
            result = None
            _LOGGER.info("Error connecting to the router")

        _LOGGER.debug("Found devices: %s", result)
        return result
