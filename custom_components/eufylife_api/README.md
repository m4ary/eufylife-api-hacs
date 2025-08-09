# EufyLife API Integration for Home Assistant

This custom integration allows you to connect your EufyLife smart scale data to Home Assistant using the EufyLife cloud API.

## Features

- 🔐 **Easy Setup**: Email/password authentication through Home Assistant UI
- ⚖️ **Weight Tracking**: Current weight and target weight sensors
- 📊 **Body Composition**: Body fat percentage, muscle mass, and BMI
- 👥 **Multi-User**: Supports multiple family members on the same scale
- 🔄 **Real-time Updates**: Automatic data synchronization every 5 minutes
- 🎨 **Home Assistant Native**: Full integration with Home Assistant's device and entity system

## Installation

### Option 1: Manual Installation

1. Copy the `eufylife_api_integration` folder to your Home Assistant `custom_components` directory:
   ```
   <config>/custom_components/eufylife_api/
   ```

2. Restart Home Assistant

3. Go to **Configuration** → **Integrations** → **Add Integration**

4. Search for "EufyLife API" and click on it

5. Enter your EufyLife account credentials

### Option 2: HACS (Future)

This integration can be added to HACS in the future for easier installation and updates.

## Configuration

1. In Home Assistant, go to **Configuration** → **Integrations**
2. Click **Add Integration** and search for "EufyLife API"
3. Enter your EufyLife email and password
4. The integration will automatically discover your devices and family members

## Supported Devices

- EufyLife Smart Scale P3 (T9150)
- Other EufyLife smart scales connected to the EufyLife app

## Sensors Created

For each family member, the integration creates the following sensors:

- **Weight** (kg) - Current weight measurement
- **Target Weight** (kg) - Weight goal
- **Body Fat** (%) - Body fat percentage  
- **Muscle Mass** (kg) - Muscle mass measurement
- **BMI** (kg/m²) - Body Mass Index

## Device Information

Each family member appears as a separate device in Home Assistant with:
- Device name: "EufyLife Customer [ID]"
- Manufacturer: EufyLife
- Model: Smart Scale
- Last update timestamp

## API Details

This integration uses the official EufyLife API endpoints:
- Authentication: `POST /v1/user/v2/email/login`
- Weight data: `GET /v1/customer/all_target`
- Detailed data: `GET /v1/customer/target/{customer_id}`

## Privacy & Security

- Credentials are stored securely in Home Assistant's encrypted storage
- Access tokens are automatically managed and refreshed
- All API communication uses HTTPS
- No data is shared with third parties

## Troubleshooting

### Common Issues

1. **Authentication Failed**
   - Verify your EufyLife email and password
   - Ensure your account has access to smart scale data

2. **No Data Available**
   - Check that your smart scale is connected to the EufyLife app
   - Ensure you have recent measurements

3. **Sensors Not Updating**
   - Check the Home Assistant logs for API errors
   - Verify your internet connection

### Debug Logging

Add this to your `configuration.yaml` to enable debug logging:

```yaml
logger:
  logs:
    custom_components.eufylife_api: debug
```

## Contributing

This integration is based on reverse-engineering the EufyLife mobile app API. Contributions are welcome!

## License

This project is licensed under the MIT License.

## Disclaimer

This is an unofficial integration. EufyLife and Eufy are trademarks of Anker Innovations Limited. 