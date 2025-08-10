# EufyLife API Integration - Debug Logging Guide

## 🐛 Troubleshooting Data Update Issues

If your EufyLife integration is not updating data as expected, follow these steps to enable comprehensive debug logging and identify the issue.

## 📝 Enable Debug Logging

Add the following to your `configuration.yaml` file:

```yaml
logger:
  default: info
  logs:
    custom_components.eufylife_api: debug
    custom_components.eufylife_api.sensor: debug
```

Then restart Home Assistant or reload the logger configuration.

## 🔍 What the Debug Logs Will Show

### 1. **Integration Setup**
- Token expiration status
- Customer IDs configuration
- Update interval settings
- Platform setup progress

### 2. **Data Coordinator**
- Update trigger timing
- API request details (with redacted tokens)
- Response processing
- Token validation
- Consecutive failure tracking

### 3. **API Communication**
- Request URLs and headers
- Response status codes
- Response data (sanitized)
- Request timing
- Error details

### 4. **Sensor Updates**
- Raw data values from API
- Data processing steps
- Sensor state changes
- Attribute updates

## 📊 Key Log Messages to Look For

### ✅ **Normal Operation**
```
[INFO] EufyLife data coordinator initialized with 300 second update interval. Next update will be triggered automatically in 300 seconds.
[INFO] Starting data update #1 at 2025-01-10 23:47:30 (interval: 300s, last successful: Never)
[INFO] Data update #1 completed successfully. Retrieved data for 2 customers. Next update in 300 seconds.
```

### ⚠️ **Token Issues**
```
[ERROR] Token expired or expiring soon! Time until expiry: -15.2 minutes. Re-authentication required. Skipping this update.
```

### 🚨 **API Problems**
```
[ERROR] API request failed with status 401. Response: {"res_code": 0, "res_msg": "Invalid token"}
[ERROR] Timeout fetching data from EufyLife API after 30 seconds. Check internet connection and API availability.
```

### 📡 **Update Timing**
```
[DEBUG] Token is valid, proceeding with API calls
[DEBUG] Making API request to: https://api.eufylife.com/v1/customer/all_target
[DEBUG] API request completed in 1.23 seconds. Status: 200
```

## 🛠️ Manual Debugging Steps

### 1. **Check Update Intervals**
Look for these log entries to verify your update interval:
```
[INFO] EufyLife integration setup with 300 second update interval for 2 customers
```

### 2. **Monitor Update Triggers**
Each update attempt will log:
```
[INFO] Starting data update #X at YYYY-MM-DD HH:MM:SS (interval: 300s, last successful: YYYY-MM-DD HH:MM:SS)
```

### 3. **Verify Token Status**
Check for token expiration:
```
[DEBUG] Token status: expires_at=1704927600, current_time=1704927300, time_until_expiry=5.0 minutes
```

### 4. **Check Customer Data**
Verify data processing:
```
[DEBUG] Processing 2 targets from API response
[DEBUG] Raw data for customer 12345678: weight=750, target_weight=700, body_fat=15, muscle_mass=350, update_time=1704927000
```

## 🔧 Common Issues and Solutions

### Issue 1: Updates Not Triggering
**Symptoms:**
- No "Starting data update" messages in logs
- Sensor values never change

**Check:**
1. Verify coordinator initialization logs
2. Look for Home Assistant core scheduler issues
3. Check if integration is properly loaded

### Issue 2: Token Expired
**Symptoms:**
```
[ERROR] Token expired or expiring soon! Time until expiry: -X.X minutes
```

**Solution:**
1. Remove and re-add the integration
2. Re-enter your EufyLife credentials
3. Check if your EufyLife account is still active

### Issue 3: API Errors
**Symptoms:**
```
[ERROR] API request failed with status 401/403/500
```

**Solutions:**
- **401/403**: Token or authentication issue - re-configure integration
- **500**: EufyLife server issue - wait and try again
- **Timeout**: Network/connectivity issue

### Issue 4: Empty Data
**Symptoms:**
```
[WARNING] Data update #X returned empty data. Consecutive failures: X
```

**Check:**
1. Verify customer IDs in configuration
2. Check if you have recent weight measurements in EufyLife app
3. Ensure EufyLife account has data

## 📈 Performance Monitoring

The debug logs include performance metrics:

```
[DEBUG] API request completed in 1.23 seconds. Status: 200
[DEBUG] Customer detail request for 12345678 completed in 0.89 seconds. Status: 200
```

Monitor these timing logs to identify slow API responses.

## 🔄 Manual Update Testing

To test updates manually, you have several options:

### Option 1: Developer Service (Recommended)
Use the built-in developer service:

1. Go to **Developer Tools** → **Services**
2. Search for "EufyLife API: Refresh EufyLife Data"
3. Click **"Call Service"**
4. Watch the logs for immediate update attempt

### Option 2: Check Sensor Attributes
**Check current sensor attributes** for debug info:
- `update_count`: Number of update attempts
- `consecutive_failures`: Number of failed updates in a row
- `last_successful_update`: Timestamp of last successful data fetch
- `update_interval`: Current update interval setting

### Option 3: Integration Reload
**Restart the integration** to trigger immediate update:
- Go to Settings → Devices & Services
- Find EufyLife API integration
- Click "Reload"

### Option 4: YAML Service Call
Add to your automations or scripts:
```yaml
service: eufylife_api.refresh_data
```

**Watch the logs** for the update sequence after any manual trigger.

## 📋 Log Analysis Checklist

When troubleshooting, check these in order:

- [ ] Integration setup completed successfully
- [ ] Token is valid and not expired
- [ ] Update interval is configured correctly
- [ ] Data coordinator is triggering updates
- [ ] API requests are being made
- [ ] API responses are successful (status 200)
- [ ] Data is being processed correctly
- [ ] Sensors are receiving updates

## 🆘 Getting Help

When reporting issues, include:

1. **Home Assistant version**
2. **Integration version** (from manifest.json)
3. **Debug logs** showing the problem (sanitize any personal data)
4. **Configuration details** (update interval, number of customers)
5. **Recent changes** to your setup

## 📱 Verify EufyLife App

Don't forget to check:
- EufyLife mobile app still works
- Recent weight measurements exist
- Account hasn't been suspended
- Scale is connected and syncing

---

With these debug logs enabled, you should be able to identify exactly where the data update process is failing and take appropriate action to resolve the issue. 