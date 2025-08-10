"""Constants for the EufyLife API integration."""

DOMAIN = "eufylife_api"

# Configuration keys
CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_UPDATE_INTERVAL = "update_interval"

# API constants
API_BASE_URL = "https://api.eufylife.com"
CLIENT_ID = "eufy-app"
CLIENT_SECRET = "8FHf22gaTKu7MZXqz5zytw"

# User-Agent version - update if EufyLife releases newer app versions
USER_AGENT_VERSION = "3.3.7"

# Default update interval in seconds (5 minutes)
DEFAULT_UPDATE_INTERVAL = 300

# Update interval options (in seconds)
UPDATE_INTERVAL_OPTIONS = {
    "1 minute": 60,
    "2 minutes": 120,
    "5 minutes": 300,
    "10 minutes": 600,
    "15 minutes": 900,
    "30 minutes": 1800,
    "1 hour": 3600,
    "2 hours": 7200,
    "6 hours": 21600,
    "12 hours": 43200,
}

# Sensor types
SENSOR_TYPES = {
    "weight": {
        "name": "Weight",
        "device_class": "weight",
        "unit": "kg",
        "icon": "mdi:scale",
    },
    "target_weight": {
        "name": "Target Weight",
        "device_class": "weight", 
        "unit": "kg",
        "icon": "mdi:target",
    },
    "body_fat": {
        "name": "Body Fat",
        "unit": "%",
        "icon": "mdi:percent",
    },
    "muscle_mass": {
        "name": "Muscle Mass",
        "device_class": "weight",
        "unit": "kg", 
        "icon": "mdi:arm-flex",
    },
    "bmi": {
        "name": "BMI",
        "unit": "kg/m²",
        "icon": "mdi:human",
    },
} 