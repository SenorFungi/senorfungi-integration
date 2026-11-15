"""Constants for the SenorFungi integration."""

DOMAIN = "senorfungi"

# EMS bus logger (options of the config entry)
CONF_EMS_ENABLED = "ems_enabled"
CONF_EMS_HOST = "ems_host"
CONF_EMS_PORT = "ems_port"
CONF_EMS_FILTER = "ems_filter_ids"
CONF_EMS_IGNORE = "ems_ignore_ids"
CONF_EMS_PAIR_SRC = "ems_pair_src"
CONF_EMS_PAIR_DST = "ems_pair_dst"
CONF_EMS_LOG_DIR = "ems_log_dir"
CONF_EMS_RETENTION = "ems_retention_days"
CONF_EMS_COMPRESS = "ems_compress_after_days"
CONF_EMS_THERMOSTAT = "ems_thermostat_id"
CONF_EMS_PRESENCE_TIMEOUT = "ems_presence_timeout"

DEFAULT_EMS_PORT = 23  # EMS-ESP telnet console
EMS_API_INFO = "http://{host}/api/system/info"  # EMS-ESP REST API, readable without login
DEFAULT_EMS_RETENTION = 90
DEFAULT_EMS_COMPRESS = 1
DEFAULT_EMS_PRESENCE_TIMEOUT = 150  # s without a telegram from the thermostat -> absent
SHARE_DIR = "/share"  # exists on Home Assistant OS and Supervised installs
EMS_LOG_SUBDIR = "ems_bus"
EMS_BROADCAST_ID = "00"

# Heating controller (config subentries). It only computes and shows a flow
# target ("shadow mode"); it never writes to the boiler.
SUBENTRY_CONTROLLER = "heating_controller"
SUBENTRY_ZONE = "heating_zone"
CONF_OUTDOOR_ENTITY = "outdoor_entity"
CONF_THERMOSTAT_ID = "thermostat_id"
CONF_PRESENCE_TIMEOUT = "presence_timeout"
CONF_DESIGN_OUTDOOR = "design_outdoor"
CONF_DESIGN_FLOW = "design_flow"
CONF_MIN_FLOW = "min_flow"
CONF_MAX_FLOW = "max_flow"
CONF_ROOM_GAIN = "room_gain"
CONF_SUMMER_LIMIT = "summer_limit"
CONF_BOOST_OFFSET = "boost_offset"
CONF_MIN_ON = "min_on_minutes"
CONF_MIN_OFF = "min_off_minutes"
CONF_ZONE_NAME = "name"
CONF_ZONE_CLIMATE = "climate_entity"
CONF_ZONE_TEMPERATURE = "temperature_entity"
CONF_ZONE_VALVE_ENTITY = "valve_entity"
CONF_ZONE_HAS_VALVE = "has_valve"
CONF_ZONE_WEIGHT = "weight"
CONF_ZONE_COMFORT = "comfort_temperature"
CONF_ZONE_ECO = "eco_temperature"

CONTROLLER_DEFAULTS = {
    CONF_PRESENCE_TIMEOUT: 150,
    CONF_DESIGN_OUTDOOR: -12.0,
    CONF_DESIGN_FLOW: 55.0,
    CONF_MIN_FLOW: 30.0,
    CONF_MAX_FLOW: 50.0,
    CONF_ROOM_GAIN: 5.0,
    CONF_SUMMER_LIMIT: 18.0,
    CONF_BOOST_OFFSET: 10.0,
    CONF_MIN_ON: 10,
    CONF_MIN_OFF: 10,
}
ZONE_DEFAULTS = {
    CONF_ZONE_HAS_VALVE: True,
    CONF_ZONE_WEIGHT: 1.0,
    CONF_ZONE_COMFORT: 21.0,
    CONF_ZONE_ECO: 19.0,
}
CONTROL_INTERVAL = 30  # s between two decisions
