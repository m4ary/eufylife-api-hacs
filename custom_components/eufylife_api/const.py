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

# Default update interval in seconds (5 minutes)
DEFAULT_UPDATE_INTERVAL = 300

# Update interval options (in seconds)
UPDATE_INTERVAL_OPTIONS = {
    "1_minute": 60,
    "2_minutes": 120,
    "5_minutes": 300,
    "10_minutes": 600,
    "15_minutes": 900,
    "30_minutes": 1800,
    "1_hour": 3600,
    "2_hours": 7200,
    "6_hours": 21600,
    "12_hours": 43200,
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