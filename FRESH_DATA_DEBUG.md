# EufyLife API - Fresh Data Debug Guide

## 🔧 **Recent Improvements (v2.0.0+)**

### Fixed: Old Data Persistence Issue
Your integration was showing old data even after deletion/re-addition because:

1. **❌ No Date Parameters**: API calls didn't specify current date/time
2. **❌ No Cache Busting**: EufyLife servers returned cached data
3. **❌ No Data Age Validation**: Integration accepted stale data without warnings

### ✅ **Implemented Solutions**

#### 1. **Fresh Data API Parameters**
```python
params = {
    "timestamp": current_timestamp,     # Current Unix timestamp
    "date": current_date,              # Current date (YYYY-MM-DD)
    "_": current_timestamp,            # Cache busting parameter
}
```

#### 2. **Data Freshness Monitoring**
```
[INFO] Customer 12345678 data: weight=750, target=700, body_fat=15, muscle=350 | 
       Last updated: 2025-01-10 14:30:15 (2.5 hours ago)
```

#### 3. **Stale Data Warnings**
```
[WARNING] Customer 12345678 data is 26.3 hours old - may be stale data from EufyLife servers
```

#### 4. **Cache Clearing on Reload**
- Manual refresh clears all coordinator tracking
- Integration unload clears runtime data
- Fresh timestamps on every API call

## 🔍 **Debug Information You'll Now See**

### **Normal Fresh Data**
```
[INFO] Customer 12345678 data: weight=755, target=700, body_fat=14, muscle=355 | 
       Last updated: 2025-01-10 16:45:22 (0.2 hours ago)
[DEBUG] API request parameters: {'timestamp': 1704927600, 'date': '2025-01-10', '_': 1704927600}
```

### **Stale Data Detection**
```
[WARNING] Customer 12345678 data is 26.3 hours old - may be stale data from EufyLife servers
```

### **Missing Timestamp Data**
```
[WARNING] Customer 12345678 has no update_time - data freshness unknown
```

## 🛠️ **Troubleshooting Steps**

### 1. **Check Data Age**
Look for the data freshness logs:
- **< 1 hour**: Fresh data ✅
- **1-6 hours**: Acceptable for daily weighing ⚠️
- **> 24 hours**: Likely stale data ❌

### 2. **Verify API Parameters**
Check logs for:
```
[DEBUG] API request parameters: {'timestamp': 1704927600, 'date': '2025-01-10', '_': 1704927600}
```

### 3. **Manual Fresh Data Test**
1. Use the **Developer Service**: `eufylife_api.refresh_data`
2. Check logs for fresh timestamp requests
3. Compare data age before/after refresh

### 4. **EufyLife App Verification**
1. Open EufyLife mobile app
2. Check if data is current there
3. Sync data if needed
4. Retry Home Assistant integration

## 📊 **Data Flow Now**

```
1. Home Assistant requests fresh data
   ↓ (with timestamp parameters)
2. EufyLife API receives timestamped request
   ↓ (should return current data)
3. Integration validates data age
   ↓ (warns if > 24 hours old)
4. Data processed and displayed
   ↓ (with age information)
5. Sensors updated with fresh values
```

## 🎯 **Expected Results**

After these improvements:
- ✅ **Fresh data requests** with proper timestamps
- ✅ **Data age monitoring** to detect stale data
- ✅ **Cache busting** to prevent server-side caching
- ✅ **Clear warnings** when data is old
- ✅ **Proper cleanup** when integration is reloaded

## 🔄 **If You Still See Old Data**

### **Integration Side (Fixed)**
- ✅ API requests now include timestamps
- ✅ Cache busting parameters added
- ✅ Data age validation implemented
- ✅ Proper cache clearing on reload

### **EufyLife Server Side (External)**
If data is still old, the issue may be:
- 🔄 EufyLife servers still serving cached data
- 📱 EufyLife app hasn't synced recent measurements
- ⏰ Scale hasn't uploaded recent data to cloud
- 🌐 EufyLife API experiencing delays

### **Next Steps for Persistent Issues**
1. **Verify in EufyLife app** that data is current
2. **Check integration logs** for data age warnings
3. **Use manual refresh** service to force updates
4. **Wait 15-30 minutes** for EufyLife cloud sync

The integration now properly requests fresh data and will warn you if EufyLife's servers are returning stale information! 🎉 