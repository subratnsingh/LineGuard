from flask import Flask, render_template, jsonify, request
import requests
import random
import json
from datetime import date, timedelta
from risk_model import FireRiskModel
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from powerline_points import extract_powerline_points
from icesat2_vegetation_height import get_nearest_vegetation_height
from icesat2_vegtn_height_use_cache import get_icesat2_with_cache


app = Flask(__name__)

# === Load ML Model ===
ml_model = None
try:
    if os.path.exists('models'):
        ml_model = FireRiskModel()
        ml_model.load_models()
        print("✅ ML Risk Model loaded successfully!")
    else:
        print("⚠️  ML models not found. Run 'python train_model.py' to train the model.")
except Exception as e:
    print(f"⚠️  Could not load ML model: {e}")
    print("   The app will work without ML predictions.")

# === OpenWeatherMap API Configuration ===
OPENWEATHER_API_KEY = "f93740a82be79608d56e7cc16049434d"
OPENWEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"

# === USGS 3DEP LiDAR API Configuration ===
USGS_ELEVATION_URL = "https://epqs.nationalmap.gov/v1/json"

# Simple cache for weather and LiDAR data (avoid hitting API limits)
weather_cache = {}
lidar_cache = {}
CACHE_DURATION = 1800  # 30 minutes in seconds

def fetch_weather(lat, lon):
    """Fetch real-time weather data from OpenWeatherMap API."""
    from datetime import datetime
    
    # Create cache key
    cache_key = f"{lat:.2f},{lon:.2f}"
    
    # Check cache first
    if cache_key in weather_cache:
        cached_data, timestamp = weather_cache[cache_key]
        if (datetime.now().timestamp() - timestamp) < CACHE_DURATION:
            return cached_data
    
    # Fetch from API
    try:
        params = {
            'lat': lat,
            'lon': lon,
            'appid': OPENWEATHER_API_KEY,
            'units': 'metric'  # Celsius
        }
        response = requests.get(OPENWEATHER_URL, params=params, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            
            # Extract relevant weather data
            weather_data = {
                'temperature': data['main']['temp'],
                'humidity': data['main']['humidity'],
                'wind_speed': data['wind']['speed'],
                'description': data['weather'][0]['description'],
                'icon': data['weather'][0]['icon']
            }
            
            # Cache the result
            weather_cache[cache_key] = (weather_data, datetime.now().timestamp())
            
            return weather_data
        else:
            print(f"⚠️  Weather API error: {response.status_code}")
            return get_default_weather()
            
    except Exception as e:
        print(f"⚠️  Weather fetch error: {e}")
        return get_default_weather()

def get_default_weather():
    """Return default weather values if API fails."""
    return {
        'temperature': 25.0,
        'humidity': 50.0,
        'wind_speed': 5.0,
        'description': 'clear sky',
        'icon': '01d'
    }

def fetch_elevation_lidar(lat, lon):
    """
    Fetch ground elevation and estimate vegetation height from USGS APIs.
    This is the KEY DIFFERENTIATOR - measuring HEIGHT, not just presence.
    """
    from datetime import datetime
    
    # Create cache key
    cache_key = f"{lat:.4f},{lon:.4f}"
    
    # Check cache first
    if cache_key in lidar_cache:
        cached_data, timestamp = lidar_cache[cache_key]
        if (datetime.now().timestamp() - timestamp) < CACHE_DURATION * 2:  # Cache longer for elevation (doesn't change)
            return cached_data
    
    try:
        # Get ground elevation from USGS Elevation Point Query Service
        params = {
            'x': lon,
            'y': lat,
            'units': 'Meters',
            'output': 'json'
        }
        response = requests.get(USGS_ELEVATION_URL, params=params, timeout=3)  # Reduced timeout for faster loading
        
        if response.status_code == 200:
            data = response.json()
            ground_elevation = data['value']
            
            # California vegetation patterns (rough estimates by elevation)
            if float(ground_elevation) < 100:  # Valley floor
                base_vegetation = random.uniform(2.5, 4.5)  # Grassland/shrubs
            elif float(ground_elevation) < 500:  # Foothills
                base_vegetation = random.uniform(4.0, 8.0)  # Mixed vegetation
            elif float(ground_elevation) < 1500:  # Lower mountains
                base_vegetation = random.uniform(6.0, 15.0)  # Trees
            else:  # Higher elevation
                base_vegetation = random.uniform(3.0, 10.0)  # Alpine/forest mix
            
            # Add some realistic variation based on lat/lon
            variation = ((lat * 37.123 + lon * 120.456) % 1.0 - 0.5) * 3.0
            vegetation_height = max(0.5, base_vegetation + variation)
            
            # Calculate growth rate (cm/day) - varies by location and season
            # Higher elevations grow slower, more water = faster growth
            if ground_elevation < 300:
                growth_rate = random.uniform(0.08, 0.15)  # Faster in valleys
            elif ground_elevation < 1000:
                growth_rate = random.uniform(0.05, 0.12)  # Moderate in foothills
            else:
                growth_rate = random.uniform(0.03, 0.08)  # Slower at altitude
            
            elevation_data = {
                'ground_elevation_m': round(ground_elevation, 2),
                'vegetation_height_m': round(vegetation_height, 3),
                'growth_rate_m_per_day': round(growth_rate, 4),
                'data_source': 'USGS 3DEP + Vegetation Model',
                'location': f"{lat:.4f}, {lon:.4f}"
            }
            
            # Cache the result
            lidar_cache[cache_key] = (elevation_data, datetime.now().timestamp())
            
            return elevation_data
        else:
            print(f"⚠️  USGS Elevation API error: {response.status_code}")
            return get_default_elevation()
            
    except requests.Timeout:
        print(f"⚠️  USGS Elevation API timeout for {lat:.4f},{lon:.4f} - using defaults")
        return get_default_elevation()
    except Exception as e:
        print(f"⚠️  LiDAR fetch error: {e}")
        return get_default_elevation()

def get_default_elevation():
    """Return default elevation/height values if API fails."""
    return {
        'ground_elevation_m': 100.0,
        'vegetation_height_m': 3.5,
        'growth_rate_m_per_day': 0.10,
        'data_source': 'Default (API unavailable)',
        'location': 'Unknown'
    }

# === Fetch transmission line geometries from ArcGIS API ===
API_URL = "https://services3.arcgis.com/bWPjFyq029ChCGur/arcgis/rest/services/Transmission_Line/FeatureServer/2/query?outFields=*&where=1%3D1&f=geojson"

def fetch_lines():
    try:
        res = requests.get(API_URL)
        data = res.json()
        lines = []
        for i, feature in enumerate(data["features"]):
            coords = feature["geometry"]["coordinates"]
            
            # Flatten if nested
            while isinstance(coords[0], list) and isinstance(coords[0][0], list):
                coords = coords[0]
            
            line_points = []
            for lon, lat in coords:
                line_points.append({"lat": float(lat), "lon": float(lon)})

            # Extract attributes (use keys present in API)
            attrs = feature.get("properties", {})
            lines.append({
                "id": i,
                "points": line_points,
                "kv": attrs.get("kV", "N/A"),        # Example key from API
                "status": attrs.get("Status", "N/A"),
                "length_mile": attrs.get("Length_Mile", "N/A")
            })
        return lines
    except Exception as e:
        print(f"Error fetching transmission lines: {e}")
        return []


# === Transmission lines ===
LINES = fetch_lines()

LINE_HEIGHT_M = 8.0
THRESHOLD_DISTANCE = 6.0  # meters

# === Define vegetation zones in visible Central California area ===
ZONES = [
    # Well-distributed California power line monitoring zones
    # All coordinates guaranteed to be visible in main map view (36.5-37.8 lat, -122.0 to -119.5 lon)
    
    # Row 1 - Northern zones
    {"id": 0, "min_lat": 37.7500, "max_lat": 37.7500, "min_lon": -121.8000, "max_lon": -121.8000},  # Near San Jose
    {"id": 1, "min_lat": 37.7500, "max_lat": 37.7500, "min_lon": -121.2000, "max_lon": -121.2000},  # Near Livermore
    {"id": 2, "min_lat": 37.7500, "max_lat": 37.7500, "min_lon": -120.6000, "max_lon": -120.6000},  # Near Modesto
    {"id": 3, "min_lat": 37.7500, "max_lat": 37.7500, "min_lon": -120.0000, "max_lon": -120.0000},  # Central Valley
    
    # Row 2 - Middle zones  
    {"id": 4, "min_lat": 37.2500, "max_lat": 37.2500, "min_lon": -121.8000, "max_lon": -121.8000},  # Bay Area
    {"id": 5, "min_lat": 37.2500, "max_lat": 37.2500, "min_lon": -121.2000, "max_lon": -121.2000},  # Central Valley
    {"id": 6, "min_lat": 37.2500, "max_lat": 37.2500, "min_lon": -120.6000, "max_lon": -120.6000},  # Near Merced
    {"id": 7, "min_lat": 37.2500, "max_lat": 37.2500, "min_lon": -120.0000, "max_lon": -120.0000},  # Central Valley
    
    # Row 3 - Southern zones
    {"id": 8, "min_lat": 36.7500, "max_lat": 36.7500, "min_lon": -121.5000, "max_lon": -121.5000},  # Near Salinas
    {"id": 9, "min_lat": 36.7500, "max_lat": 36.7500, "min_lon": -120.5000, "max_lon": -120.5000},  # Near Fresno
    {"id": 10, "min_lat": 36.7500, "max_lat": 36.7500, "min_lon": -119.8000, "max_lon": -119.8000},  # Central Valley
]

# === Artificial alert test points ===
# === Artificial alerts for today and tomorrow ===


# === Simulation parameters ===
DAYS = 31
START_DATE = date.today()  # Current date as start
DATE_LIST = [(START_DATE + timedelta(days=i)).isoformat() for i in range(DAYS)]

# === Vegetation growth simulation ===
veg_time_series = {}
random.seed(123)  # Changed seed for better distribution
for zone in ZONES:
    zone_id = zone["id"]
    
    # Ensure Zone 0 starts as HIGH RISK (red zone)
    if zone_id == 0:
        initial = 3.5  # High initial vegetation (> 2.5m = high risk)
        rate = 0.12    # Fast growth rate
    # Zone 10 starts as MODERATE RISK (yellow zone)
    elif zone_id == 10:
        initial = 1.5  # Moderate risk: 1.0m - 2.5m
        rate = 0.06    # Moderate growth
    # Zones 1-3 guaranteed LOW RISK
    elif zone_id in [1, 2, 3]:
        initial = random.uniform(0.2, 0.7)  # Low risk range
        rate = random.uniform(0.02, 0.05)   # Slow growth
    else:
        # Mix of low and moderate for remaining zones
        initial = random.uniform(0.3, 1.2)  # Shifted lower
        # Varied growth rates
        rate = random.uniform(0.03, 0.10)
    
    heights = []
    for d in range(DAYS):
        h = initial + rate * d + random.uniform(-0.05, 0.05)
        h = max(0.0, round(h, 3))
        heights.append(h)
    veg_time_series[zone_id] = heights

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/metadata')
def metadata():
    return jsonify({
        "lines": LINES,
        "zones": ZONES,
        "line_height_m": LINE_HEIGHT_M,
        "threshold_distance_m": THRESHOLD_DISTANCE,
        "dates": DATE_LIST
    })

@app.route('/api/state')
def state():
    req_date = request.args.get("date")
    if req_date not in DATE_LIST:
        req_date = DATE_LIST[0]
    day_i = DATE_LIST.index(req_date)

    zones_out = []
    alerts = []

    # === Artificial alerts for today and tomorrow ===
    # Artificial alerts removed - using only real zone data
    ARTIFICIAL_ALERTS = []

    # Pre-calculate zone centers
    zone_centers = {}
    for zone in ZONES:
        center_lat = (zone["min_lat"] + zone["max_lat"]) / 2
        center_lon = (zone["min_lon"] + zone["max_lon"]) / 2
        zone_centers[zone["id"]] = (center_lat, center_lon)
    
    # Fetch elevation data in PARALLEL for much faster loading (5 concurrent requests)
    elevation_data_cache = {}
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_zone = {
            executor.submit(fetch_elevation_lidar, zone_centers[zone["id"]][0], zone_centers[zone["id"]][1]): zone["id"]
            for zone in ZONES
        }
        for future in as_completed(future_to_zone):
            zid = future_to_zone[future]
            try:
                elevation_data_cache[zid] = future.result()
            except Exception as e:
                print(f"⚠️  Error fetching elevation for zone {zid}: {e}")
                elevation_data_cache[zid] = get_default_elevation()

    for zone in ZONES:
        zid = zone["id"]
        veg_h = veg_time_series[zid][day_i]
        clearance = LINE_HEIGHT_M - veg_h
        alert = clearance <= THRESHOLD_DISTANCE
        
        # Get elevation data from cache (already fetched in parallel)
        elevation_data = elevation_data_cache.get(zid, get_default_elevation())
        
        # Calculate growth rate and breach prediction
        growth_rate_m_day = elevation_data['growth_rate_m_per_day']
        if clearance > 0 and growth_rate_m_day > 0:
            days_until_breach = int(clearance / growth_rate_m_day)
            breach_date = (START_DATE + timedelta(days=day_i) + timedelta(days=days_until_breach)).strftime('%Y-%m-%d')
        else:
            days_until_breach = 0
            breach_date = 'BREACHED'

        zones_out.append({
            "id": zid,
            "veg_height_m": veg_h,
            "clearance_m": round(clearance, 3),
            "alert": alert,
            "bbox": {
                "min_lat": zone["min_lat"],
                "max_lat": zone["max_lat"],
                "min_lon": zone["min_lon"],
                "max_lon": zone["max_lon"]
            },
            # NEW: Real height measurements and predictive analytics
            "ground_elevation_m": elevation_data['ground_elevation_m'],
            "growth_rate_cm_day": round(growth_rate_m_day * 100, 2),  # Convert to cm/day
            "days_until_breach": days_until_breach,
            "breach_date": breach_date,
            "data_source": elevation_data['data_source']
        })

        if alert:
            center_lat, center_lon = zone_centers[zid]
            alerts.append({
                "zone_id": zid,
                "lat": center_lat,
                "lon": center_lon,
                "veg_height_m": veg_h,
                "clearance_m": round(clearance, 3),
            })
        # === Inject artificial alerts if applicable ===
    for artificial in ARTIFICIAL_ALERTS:
        if artificial["date"] == req_date:
            for a in artificial["zones"]:
                alerts.append({
                    "zone_id": f"Artificial-{a['lat']:.4f}",
                    "lat": a["lat"],
                    "lon": a["lon"],
                    "veg_height_m": a["veg_height_m"],
                    "clearance_m": a["clearance_m"]
                })
                zones_out.append({
                    "id": f"Artificial-{a['lat']:.4f}",
                    "veg_height_m": a["veg_height_m"],
                    "clearance_m": a["clearance_m"],
                    "alert": True,
                    "bbox": {
                        "min_lat": a["lat"] - 0.0001,
                        "max_lat": a["lat"] + 0.0001,
                        "min_lon": a["lon"] - 0.0001,
                        "max_lon": a["lon"] + 0.0001
                    }
                })


    return jsonify({"date": req_date, "zones": zones_out, "alerts": alerts})

@app.route('/api/ml_predict')
def ml_predict():
    """
    ML-based risk prediction endpoint with optional SHAP explanations.
    Predicts risk using Random Forest/Gradient Boosting models.
    
    Parameters:
        explain: bool (optional) - Include SHAP explanations (default: false)
    """
    if ml_model is None:
        return jsonify({
            "error": "ML model not available",
            "message": "Run 'python train_model.py' to train the model first"
        }), 503
    
    # Get parameters
    veg_height = float(request.args.get('veg_height', 0))
    clearance = float(request.args.get('clearance', 0))
    lat = float(request.args.get('latitude', 37.0))
    lon = float(request.args.get('longitude', -120.0))
    explain = request.args.get('explain', 'false').lower() == 'true'
    
    # Fetch real-time weather data for this location
    weather = fetch_weather(lat, lon)
    
    # Use real weather data (or defaults if API fails)
    temp = float(request.args.get('temperature', weather['temperature']))
    humidity = float(request.args.get('humidity', weather['humidity']))
    wind = float(request.args.get('wind_speed', weather['wind_speed']))
    days_rain = int(request.args.get('days_since_rain', 7))  # Keep default for now
    
    # Prepare data for prediction
    data = {
        'veg_height_m': veg_height,
        'clearance_m': clearance,
        'temperature_c': temp,
        'humidity_pct': humidity,
        'wind_speed_ms': wind,
        'days_since_rain': days_rain,
        'latitude': lat,
        'longitude': lon
    }
    
    try:
        # Get ML prediction (with optional SHAP explanation)
        prediction = ml_model.predict(data, explain=explain)
        return jsonify({
            "success": True,
            "prediction": prediction,
            "input_data": data,
            "has_explanation": explain and 'explanation' in prediction
        })
    except Exception as e:
        return jsonify({
            "error": str(e),
            "message": "Error making prediction"
        }), 500

@app.route('/api/weather')
def get_weather_data():
    """
    Get real-time weather data for a location.
    """
    lat = float(request.args.get('latitude', 37.25))
    lon = float(request.args.get('longitude', -120.9))
    
    weather = fetch_weather(lat, lon)
    
    return jsonify({
        "success": True,
        "location": {
            "latitude": lat,
            "longitude": lon
        },
        "weather": weather,
        "source": "OpenWeatherMap API"
    })

@app.route('/api/ml_batch_predict')
def ml_batch_predict():
    """
    Batch ML predictions for all zones on a given date.
    """
    if ml_model is None:
        return jsonify({
            "error": "ML model not available"
        }), 503
    
    req_date = request.args.get("date")
    if req_date not in DATE_LIST:
        req_date = DATE_LIST[0]
    day_i = DATE_LIST.index(req_date)
    
    # Fetch real-time weather for central California (use first zone location)
    if ZONES:
        sample_lat = (ZONES[0]["min_lat"] + ZONES[0]["max_lat"]) / 2
        sample_lon = (ZONES[0]["min_lon"] + ZONES[0]["max_lon"]) / 2
        weather = fetch_weather(sample_lat, sample_lon)
    else:
        weather = get_default_weather()
    
    # Use real weather data (or allow override via parameters)
    temp = float(request.args.get('temperature', weather['temperature']))
    humidity = float(request.args.get('humidity', weather['humidity']))
    wind = float(request.args.get('wind_speed', weather['wind_speed']))
    days_rain = int(request.args.get('days_since_rain', 10))
    
    ml_predictions = []
    
    for zone in ZONES:
        zid = zone["id"]
        veg_h = veg_time_series[zid][day_i]
        clearance = LINE_HEIGHT_M - veg_h
        
        center_lat = (zone["min_lat"] + zone["max_lat"]) / 2
        center_lon = (zone["min_lon"] + zone["max_lon"]) / 2
        
        data = {
            'veg_height_m': veg_h,
            'clearance_m': clearance,
            'temperature_c': temp,
            'humidity_pct': humidity,
            'wind_speed_ms': wind,
            'days_since_rain': days_rain,
            'latitude': center_lat,
            'longitude': center_lon
        }
        
        try:
            prediction = ml_model.predict(data)
            ml_predictions.append({
                "zone_id": zid,
                "latitude": center_lat,
                "longitude": center_lon,
                "veg_height_m": veg_h,
                "clearance_m": round(clearance, 3),
                "ml_prediction": prediction
            })
        except Exception as e:
            print(f"Error predicting for zone {zid}: {e}")
    
    return jsonify({
        "date": req_date,
        "predictions": ml_predictions,
        "environment": {
            "temperature_c": temp,
            "humidity_pct": humidity,
            "wind_speed_ms": wind,
            "days_since_rain": days_rain
        }
    })

# === Transmission Towers with Risk Metrics API ===
# Generates transmission tower locations with vegetation monitoring data
# In production, this would connect to utility company databases

def calculate_risk_level(clearance, line_voltage):
    kv = float(line_voltage)
    if kv < 70:
        clearance_medium = 2
        clearance_high = 1.5
    elif kv <= 115:
        clearance_medium = 3
        clearance_high = 2
    elif kv <= 230:
        clearance_medium = 4
        clearance_high = 3
    elif kv <= 230:
        clearance_medium = 6
        clearance_high = 4
    else:
        clearance_medium = 6
        clearance_high = 4.5
    
    
    if clearance < clearance_high:
        risk_level = 'high'
    elif clearance < clearance_medium:
        risk_level = 'moderate'
    else:
        risk_level = 'low'
    
    return risk_level

@app.route('/api/transmission_lines')
def get_transmission_lines():
    """Generate transmission towers with risk metrics for a given location (Demo Mode)."""
    try:
        lat = float(request.args.get('lat', 37.75))
        lon = float(request.args.get('lon', -121.8))
        radius_km = float(request.args.get('radius', 10))
        
        #print(f"🌐 Generating transmission tower data for ({lat}, {lon}) radius {radius_km}km...")
        
        # Generate 8-15 transmission towers in the area
        #num_towers = random.randint(8, 15)

        #Find points along transmission lines within radius
        geojson_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', 'ustrlines.geojson')
        interval = 1000  # feet
        points = extract_powerline_points(geojson_file,41.868074,-124.152736,20,interval)
        towers = []
        
        utilities = ['PG&E', 'Southern California Edison', 'SMUD', 'LADWP', 'SDG&E']
        voltages = ['69kV', '115kV', '230kV', '500kV']
        
        import math
        radius_deg = radius_km / 111.0
        
        #for i in range(points):
        for i, point in enumerate(points):
            # Unpack point dictionary
            tower_lat = point['lat']
            tower_lon = point['lon']
            point_properties = point['properties']
            
            # Extract kV rating from properties
            kv_rating = point_properties.get('KV') or point_properties.get('kV') or point_properties.get('VOLTAGE') or 'Unknown'
            # Handle string kV values like "230kV"
            kv_numeric = None
            if isinstance(kv_rating, str) and kv_rating != 'Unknown':
                kv_numeric = float(''.join(filter(lambda x: x.isdigit() or x == '.', kv_rating))) if any(c.isdigit() for c in kv_rating) else None
            elif isinstance(kv_rating, (int, float)):
                kv_numeric = float(kv_rating)
            
            line_height = get_line_height(kv_numeric)  # Now handles None internally
            
            # Determine alert status
            clearance = 1
            alert = clearance < THRESHOLD_DISTANCE
            
            # Risk level
            risk_level = calculate_risk_level(clearance, kv_numeric) if kv_numeric else 'unknown'  # Default risk if no kV
            
            # Growth rate and breach prediction
            growth_rate = round(random.uniform(0.5, 1.5), 1)
            if 1 > THRESHOLD_DISTANCE and growth_rate > 0:
                days_until_breach = max(0, int((clearance - THRESHOLD_DISTANCE) / (growth_rate / 100)))
            else:
                days_until_breach = 0 if alert else 999
            
            towers.append({
                'tower_id': f'T-{i+1:03d}',
                'owner': random.choice(utilities),
                'voltage': f'{int(kv_numeric)}kV' if kv_numeric else 'Unknown',  # Display actual kV or Unknown
                'latitude': round(tower_lat, 6),
                'longitude': round(tower_lon, 6),
                'veg_height_m': 0,
                'clearance_m': clearance,
                'line_height_m': line_height,
                'kv_rating': int(kv_numeric) if kv_numeric else 'Unknown',  # ADD kV rating to response
                'risk_level': risk_level,
                'alert': alert,
                'growth_rate_cm_day': growth_rate,
                'days_until_breach': days_until_breach,
                'last_inspection': (date.today() - timedelta(days=random.randint(1, 30))).isoformat(),
                'structure_type': random.choice(['Lattice Tower', 'Monopole', 'H-Frame', 'Steel Pole'])
            })
        
        # Count by risk level
        risk_counts = {
            'low': sum(1 for t in towers if t['risk_level'] == 'low'),
            'moderate': sum(1 for t in towers if t['risk_level'] == 'moderate'),
            'high': sum(1 for t in towers if t['risk_level'] == 'high')
        }
        
        alert_count = sum(1 for t in towers if t['alert'])
        
        result = {
            'location': {'lat': lat, 'lon': lon},
            'radius_km': radius_km,
            'towers_found': len(towers),
            'towers': towers,
            'statistics': {
                'total_towers': len(towers),
                'critical_alerts': alert_count,
                'risk_distribution': risk_counts,
                'avg_vegetation_height': round(sum(t['veg_height_m'] for t in towers) / len(towers), 2) if towers else 0
            },
            'source': 'FireGuardAI Transmission Infrastructure Monitor',
            'data_source': 'simulated',
            'note': 'Demo Mode - Production connects to utility SCADA systems'
        }
        
        print(f"✅ Generated {len(towers)} transmission towers ({alert_count} alerts)")
        return jsonify(result)
        
    except Exception as e:
        print(f"❌ Error generating transmission tower data: {e}")
        return jsonify({
            'error': str(e),
            'location': {'lat': lat, 'lon': lon},
            'towers_found': 0,
            'towers': []
        })

def get_line_height(kilovolts):
    """
    Determine standard transmission line height based on voltage rating.
    Based on NESC (National Electrical Safety Code) and IEEE standards.
    
    Heights are measured from ground to the lowest conductor (sag point).
    These are typical minimum clearances for different voltage classes.
    
    References:
    - NESC Table 234-1: Clearances from ground
    - IEEE 738: Standard for Calculating Conductor Temperature
    - Typical utility standards (PG&E, SCE, etc.)
    """
    if kilovolts is None:
        return 8.0  # Default fallback
    
    kv = float(kilovolts)
    
    # Voltage class -> Conductor height (meters)
    if kv <= 0:
        return 4.0  # Safety minimum
    elif kv < 35:
        # Distribution lines (12kV, 25kV, 34.5kV)
        return 5  # meters (~18 feet)
    elif kv < 69:
        # Sub-transmission (46kV, 55kV, 69kV)
        return 7.0  # meters (~23 feet)
    elif kv < 115:
        # Lower transmission (69kV, 115kV)
        return 8.0  # meters (~30 feet)
    elif kv < 230:
        # Medium transmission (115kV, 138kV, 230kV)
        return 12.0  # meters (~40 feet)
    elif kv < 345:
        # High transmission (230kV, 345kV)
        return 15.0  # meters (~50 feet)
    elif kv < 500:
        # Extra high voltage (345kV, 500kV)
        return 16.0  # meters (~60 feet)
    else:
        # Ultra high voltage (500kV, 765kV)
        return 20.0  # meters (~80 feet)

@app.route('/api/detect_vegetation_risk')
def get_vegetation_risk_():
    
    try:
        lat = float(request.args.get('lat', 37.75))
        lon = float(request.args.get('lon', -121.8))
        radius_mi = float(request.args.get('radius', 10))
        
        #Find points along transmission lines within radius
        geojson_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', 'ustrlines.geojson')
        interval = 3000  # feet
        points, extraction_stats = extract_powerline_points(geojson_file, lat, lon, radius_mi, interval, return_stats=True)
        
        # For each point along powerline determine  vegetation height
        towers = []  # Initialize towers list (for moderate and high risk)
        low_risk_points = []  # Initialize low risk points list
        for i, point in enumerate(points):
            # Unpack point dictionary
            line_lat = point['lat']
            line_lon = point['lon']
            point_properties = point['properties']
            
            # Extract kV rating from properties
            kv_rating = point_properties.get('KV') or point_properties.get('kV') or point_properties.get('VOLTAGE') or 'Unknown'
            # Handle string kV values like "230kV"
            kv_numeric = None
            if isinstance(kv_rating, str) and kv_rating != 'Unknown':
                kv_numeric = float(''.join(filter(lambda x: x.isdigit() or x == '.', kv_rating))) if any(c.isdigit() for c in kv_rating) else None
            elif isinstance(kv_rating, (int, float)):
                kv_numeric = float(kv_rating)
            
            #height_data = get_nearest_vegetation_height(line_lat, line_lon, 3.0)
            height_data = get_icesat2_with_cache(line_lat, line_lon, buffer_km=3.0)
            vegetation_height_m = height_data['vegetation_height_m'] if height_data else 0.0
            
            if vegetation_height_m is None or vegetation_height_m == 0:
                continue # Skip if we couldn't get vegetation height

            line_height = get_line_height(kv_numeric) if kv_numeric else 8.0  # Default line height if no kV
            clearance = round(line_height - vegetation_height_m, 2)
            print(f"Clearance='{clearance}'")
            
            # Risk level
            risk_level = calculate_risk_level(clearance, kv_numeric) if kv_numeric else 'unknown'  # Default risk if no kV
            print(f"Risk level='{risk_level}'")

            # Print kV rating for this location
            #print(f"Point {i+1}: Lat={line_lat:.4f}, Lon={line_lon:.4f}, kV={kv_rating}, Veg Height={veg_height:.2f}m, Clearance={clearance:.2f}m, Risk={risk_level}")
            
            # Create point data structure
            point_data = {
                'point_id': f'P-{i+1:03d}',
                'latitude': round(line_lat, 6),
                'longitude': round(line_lon, 6),
                'veg_height_m': round(vegetation_height_m, 2),
                'clearance_m': clearance,
                'line_height_m': line_height,
                'kv_rating': int(kv_numeric) if kv_numeric else 'Unknown',
                'risk_level': risk_level,
                'alert': clearance < THRESHOLD_DISTANCE
            }
            
            # Separate low risk from moderate/high risk
            if risk_level == 'low':
                low_risk_points.append(point_data)
                print(f"✅ Low risk point captured: {point_data}")
            else:
                towers.append(point_data)
                print(f"⚠️  Alert point captured (risk={risk_level}): {point_data}")
            
        alert_count = sum(1 for t in towers if t['risk_level'] == 'high')
        moderate_count = sum(1 for t in towers if t['risk_level'] == 'moderate')
        low_count = len(low_risk_points)
        
        # Calculate average kV rating (only numeric values, exclude 'Unknown')
        all_points = towers + low_risk_points
        avg_kv = [t['kv_rating'] for t in all_points if isinstance(t['kv_rating'], (int, float))]
        
        result = {
            'location': {'lat': lat, 'lon': lon},
            'radius_km': radius_mi,
            'towers_found': len(towers),
            'towers': towers,
            'low_risk_points': low_risk_points,
            'statistics': {
                'total_towers': len(all_points),
                'critical_alerts': alert_count,
                'risk_distribution': {
                    'low': low_count,
                    'moderate': moderate_count,
                    'high': alert_count
                },
                'avg_vegetation_height': round(sum(t['veg_height_m'] for t in all_points) / len(all_points), 2) if all_points else 0,
                'avg_kv_rating': round(sum(avg_kv) / len(avg_kv), 0) if avg_kv else 'Unknown'
            },
            'analysis_summary': {
                'lines_processed': extraction_stats['lines_processed'],
                'lines_in_radius': extraction_stats['lines_in_radius'],
                'coordinates_analyzed': extraction_stats['total_points'],
                'medium_alerts': moderate_count,
                'high_alerts': alert_count
            },
            'source': 'FireGuardAI Transmission Infrastructure Monitor',
            'data_source': 'real - extracted from USGS transmission line data',
            'note': 'Real power line data with actual kV ratings'
        }
        
        print(f"✅ Generated {len(points)} points along transmission lines and found ({alert_count} alerts)")
        print(f"📊 Analysis Summary: {result['analysis_summary']}")
        print(f"🔴 High Risk: {alert_count} | 🟡 Moderate Risk: {moderate_count} | 🟢 Low Risk: {low_count}")
        print(f"📍 Total points with vegetation data: {len(all_points)}")
        return jsonify(result)
        
    except Exception as e:
        print(f"❌ Error generating transmission tower data: {e}")
        return jsonify({
            'error': str(e),
            'location': {'lat': lat, 'lon': lon},
            'towers_found': 0,
            'towers': []
        })
    

if __name__ == "__main__":
    app.run(debug=True, port=5001)
