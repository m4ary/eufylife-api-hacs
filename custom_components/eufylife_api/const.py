"""Constants for the EufyLife API integration."""

DOMAIN = "eufylife_api"

# Configuration keys
CONF_EMAIL = "email"
CONF_PASSWORD = "password"

# API constants
API_BASE_URL = "https://api.eufylife.com"
CLIENT_ID = "eufy-app"
CLIENT_SECRET = "8FHf22gaTKu7MZXqz5zytw"

# Update interval
UPDATE_INTERVAL = 300  # 5 minutes

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