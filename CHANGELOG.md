# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2025-01-09

### Added
- **Configurable Update Intervals**: Users can now choose update frequency from 1 minute to 12 hours
- **Options Flow Support**: Change update interval after setup without recreating the integration
- **Dynamic Interval Updates**: Changes take effect immediately without restarting Home Assistant
- **Interval Display**: Current update interval shown in sensor attributes
- **Re-authentication Support**: Improved handling for expired tokens

### Changed
- **Default Update Interval**: Still 5 minutes but now user-configurable
- **Data Coordinator**: Enhanced to support dynamic interval changes
- **Sensor Attributes**: Added update interval information to all sensors
- **Logging**: Improved logging for interval changes and coordinator status

### Available Update Intervals
- 1 minute (for frequent weighing sessions)
- 2 minutes  
- 5 minutes (recommended default)
- 10, 15, 30 minutes (moderate usage)
- 1, 2, 6, 12 hours (occasional use)

### Technical Changes
- Enhanced config flow with update interval selection
- Added options flow for post-setup configuration
- Improved data coordinator with configurable intervals
- Updated strings.json with interval option translations
- Added config entry update listener for dynamic changes

## [1.0.0] - 2025-01-09

### Added
- Initial release of EufyLife API integration for Home Assistant
- Email/password authentication through Home Assistant UI
- Multi-user support for family members on the same scale
- Weight tracking sensors (current weight, target weight)
- Body composition sensors (body fat, muscle mass, BMI)
- Automatic device discovery and setup
- Data coordinator for efficient API polling every 5 minutes
- Token management with 30-day expiry handling
- HACS compatibility for easy installation
- Comprehensive error handling and logging
- Support for EufyLife Smart Scale P3 and other models

### Features
- Native Home Assistant integration with config flow
- Individual devices for each family member
- Sensor entities with proper device classes and units
- State attributes including last update timestamp
- Secure credential storage in Home Assistant
- Real-time weight and body composition monitoring

### API Endpoints
- Authentication via `/v1/user/v2/email/login`
- Weight data from `/v1/customer/all_target`
- Detailed customer data from `/v1/customer/target/{customer_id}`

## [Unreleased]

### Planned
- Automatic token refresh functionality
- Historical weight data trends
- Additional body composition metrics
- Goal tracking and notifications
- Enhanced error recovery mechanisms 