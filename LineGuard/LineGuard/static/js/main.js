// Initialize map centered on Central California - optimized for zone grid
let map = L.map('map').setView([37.25, -120.9], 7);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { 
    maxZoom: 19,
    attribution: '© OpenStreetMap contributors'
}).addTo(map);

let zoneLayers = {}, alertMarkers = [], lineLayers = [];
let isPlaying = false, currentDate = null;
let intervalId = null;
let dateList = [];
let isUpdatingSlider = false; // Flag to prevent slider feedback loop

function colorForHeight(h, clearance) {
    if (clearance <= 6.0) return 'red'; // High risk / alert
    if (h < 1.0) return 'green'; // Low vegetation
    if (h < 2.5) return 'yellow'; // Moderate vegetation
    return 'red'; // High vegetation
}

function getRiskLevel(h, clearance) {
    if (clearance <= 6.0) return 'high';
    if (h < 1.0) return 'low';
    if (h < 2.5) return 'moderate';
    return 'high';
}

async function updateMLRiskAssessment(zones) {
    // Find the highest risk zone
    let highestRiskZone = null;
    let highestRisk = -1;
    
    zones.forEach(zone => {
        const riskLevel = getRiskLevel(zone.veg_height_m, zone.clearance_m);
        const riskValue = riskLevel === 'high' ? 3 : (riskLevel === 'moderate' ? 2 : 1);
        if (riskValue > highestRisk) {
            highestRisk = riskValue;
            highestRiskZone = zone;
        }
    });
    
    if (!highestRiskZone) {
        // No zones, show default
        document.getElementById('mlRiskLevel').innerText = '-';
        document.getElementById('mlRiskScore').innerText = '-';
        document.getElementById('mlConfidence').innerText = '-';
        document.getElementById('growthRateDisplay').innerText = '-';
        document.getElementById('breachPrediction').innerText = '-';
        return;
    }
    
    // Update Growth Rate and Breach Prediction in Live Metrics
    if (highestRiskZone.growth_rate_cm_day) {
        document.getElementById('growthRateDisplay').innerText = `${highestRiskZone.growth_rate_cm_day} cm/day`;
    }
    
    if (highestRiskZone.days_until_breach !== undefined) {
        if (highestRiskZone.days_until_breach === 0 || highestRiskZone.breach_date === 'BREACHED') {
            document.getElementById('breachPrediction').innerHTML = '<span style="color: #ef4444; font-weight: bold;">🔴 BREACHED</span>';
        } else {
            document.getElementById('breachPrediction').innerText = `${highestRiskZone.days_until_breach} days`;
        }
    }
    
    // Call ML prediction API
    try {
        const params = new URLSearchParams({
            veg_height: highestRiskZone.veg_height_m,
            clearance: highestRiskZone.clearance_m,
            temperature: 25, // Default weather values
            humidity: 50,
            wind_speed: 5,
            days_since_rain: 7,
            latitude: (highestRiskZone.bbox.min_lat + highestRiskZone.bbox.max_lat) / 2,
            longitude: (highestRiskZone.bbox.min_lon + highestRiskZone.bbox.max_lon) / 2
        });
        
        const response = await fetch(`/api/ml_predict?${params}`);
        const data = await response.json();
        
        if (data.success && data.prediction) {
            const pred = data.prediction;
            
            // Update ML Risk Level with emoji and color
            const riskEmoji = {
                'Low': '🟢',
                'Moderate': '🟡',
                'High': '🟠',
                'Critical': '🔴'
            };
            document.getElementById('mlRiskLevel').innerText = `${riskEmoji[pred.risk_level] || ''} ${pred.risk_level}`;
            
            // Update Risk Score
            document.getElementById('mlRiskScore').innerText = `${pred.risk_percentage.toFixed(1)}%`;
            
            // Update Confidence
            document.getElementById('mlConfidence').innerText = `${(pred.confidence * 100).toFixed(0)}%`;
        } else {
            throw new Error('Prediction failed');
        }
    } catch (error) {
        console.error('ML Prediction error:', error);
        document.getElementById('mlRiskLevel').innerText = 'Error';
        document.getElementById('mlRiskScore').innerText = '-';
        document.getElementById('mlConfidence').innerText = '-';
    }
}

function getIconForRisk(riskLevel) {
    const icons = {
        'low': '🟢',
        'moderate': '🟡', 
        'high': '🔴'
    };
    return icons[riskLevel] || '⚪';
}

async function fetchMetadata() {
    const res = await fetch('/api/metadata');
    const data = await res.json();
    dateList = data.dates;
    
    // Set initial date label immediately
    if (dateList.length > 0) {
        const dayLabel = document.getElementById('dayLabel');
        if (dayLabel) {
            dayLabel.innerText = dateList[0];
        }
    }
    
    return data;
}

function drawLines(lines) {
    lines.forEach(l => {
        const latlngs = l.points.map(p => [p.lat, p.lon]);
        const poly = L.polyline(latlngs, { 
            color: '#2d3748', 
            weight: 3,
            opacity: 0.7,
            smoothFactor: 1
        }).addTo(map);

        // Bind a tooltip showing KV, length, and status
        const tooltipContent = `
            <div style="font-family: 'Poppins', sans-serif;">
                <strong style="color: #764ba2;"><i class="fas fa-bolt"></i> Transmission Line ${l.id}</strong><br>
                <strong>kV:</strong> ${l.kv}<br>
                <strong>Length:</strong> ${l.length_mile} miles<br>
                <strong>Status:</strong> ${l.status}
            </div>
        `;
        poly.bindTooltip(tooltipContent, { sticky: true });

        lineLayers.push(poly);
    });
}


async function updateMapByDate(dateStr) {
    try {
        console.log('📡 Fetching state for date:', dateStr);
        const res = await fetch(`/api/state?date=${dateStr}`);
        if (!res.ok) {
            throw new Error(`API error: ${res.status}`);
        }
        const data = await res.json();
        console.log('✅ State fetched, zones:', data.zones.length);
        
        const dayLabel = document.getElementById('dayLabel');
        if (dayLabel) {
            dayLabel.innerText = dateStr;
        }

    // Update slider (without triggering input event)
    isUpdatingSlider = true;
    const index = dateList.indexOf(dateStr);
    if (index >= 0 && index < dateList.length) {
        document.getElementById('dayRange').value = index;
    }
    // Use setTimeout to reset flag after DOM update
    setTimeout(() => { isUpdatingSlider = false; }, 100);

    // Clear previous layers
    Object.values(zoneLayers).forEach(layer => map.removeLayer(layer));
    zoneLayers = {};
    alertMarkers.forEach(m => map.removeLayer(m));
    alertMarkers = [];

    const alertItems = document.getElementById('alertItems');
    const alertItemsPredictor = document.getElementById('alertItemsPredictor');
    alertItems.innerHTML = '';
    alertItemsPredictor.innerHTML = '';
    
    // Calculate statistics
    let totalHeight = 0;
    let alertCount = 0;
    let riskCounts = { low: 0, moderate: 0, high: 0 };

    data.zones.forEach(z => {
        // Calculate center of zone
        const centerLat = (z.bbox.min_lat + z.bbox.max_lat) / 2;
        const centerLon = (z.bbox.min_lon + z.bbox.max_lon) / 2;

        const riskLevel = getRiskLevel(z.veg_height_m, z.clearance_m);
        
        // Create custom icon based on risk level
        let iconHtml, iconClass;
        
        // Better-looking markers with correct positioning
        let markerColor, markerLabel;
        if (riskLevel === 'low') {
            markerColor = '#10b981';
            markerLabel = '✓';
        } else if (riskLevel === 'moderate') {
            markerColor = '#f59e0b';
            markerLabel = '!';
        } else {
            markerColor = '#ef4444';
            markerLabel = '⚠';
        }

        const zoneIcon = L.divIcon({
            className: 'clean-marker',
            html: `<div style="
            background-color: ${markerColor}; 
                width: 30px; 
                height: 30px; 
                border-radius: 50%; 
                border: 3px solid white; 
                box-shadow: 0 3px 8px rgba(0,0,0,0.3);
                display: flex;
                align-items: center;
                justify-content: center;
                color: white;
                font-weight: bold;
                font-size: 16px;
            ">${markerLabel}</div>`,
            iconSize: [30, 30],
            iconAnchor: [15, 15]
        });

        const marker = L.marker([centerLat, centerLon], { icon: zoneIcon }).addTo(map);
        
        // Enhanced tooltip with consistent risk labels
        const riskLabels = {
            'low': '<span style="color: #10b981; font-weight: bold;">🟢 Low Risk</span>',
            'moderate': '<span style="color: #f59e0b; font-weight: bold;">🟡 Moderate Risk</span>',
            'high': '<span style="color: #ef4444; font-weight: bold;">🔴 High Risk</span>'
        };
        
        marker.bindTooltip(`
            <div style="font-family: 'Poppins', sans-serif; padding: 8px; max-width: 300px;">
                <strong style="font-size: 1.1em; color: #1e293b;">Zone ${z.id}</strong><br>
                <div style="margin-top: 4px; padding: 4px 0; border-top: 1px solid #e2e8f0;">
                    Risk Level: ${riskLabels[riskLevel]}<br>
                    <i class="fas fa-tree"></i> Vegetation: <strong>${z.veg_height_m}m</strong><br>
                    <i class="fas fa-ruler-vertical"></i> Clearance: <strong>${z.clearance_m}m</strong><br>
                    ${z.growth_rate_cm_day ? `<i class="fas fa-chart-line"></i> Growth: <strong>${z.growth_rate_cm_day} cm/day</strong><br>` : ''}
                    ${z.days_until_breach ? `<i class="fas fa-calendar-alt"></i> Breach: <strong>${z.days_until_breach} days</strong> (${z.breach_date})<br>` : ''}
                    ${z.ground_elevation_m ? `<i class="fas fa-mountain"></i> Elevation: ${z.ground_elevation_m}m<br>` : ''}
                    <small style="color: #64748b;"><i class="fas fa-map-pin"></i> ${centerLat.toFixed(4)}, ${centerLon.toFixed(4)}</small><br>
                    <small style="color: #94a3b8; font-style: italic;">${z.data_source || 'Simulated Data'}</small>
                </div>
            </div>
        `, { sticky: true });

        zoneLayers[z.id] = marker;
        
        // Update statistics
        totalHeight += z.veg_height_m;
        if (z.alert) alertCount++;
        riskCounts[riskLevel]++;
    });
    
    // Update statistics display
    const avgHeight = data.zones.length > 0 ? (totalHeight / data.zones.length).toFixed(2) : 0;
    document.getElementById('alertCount').innerText = data.alerts.length;
    document.getElementById('zoneCount').innerText = data.zones.length;
    document.getElementById('avgHeight').innerText = avgHeight + 'm';
    
    // Update risk level counts in legend
    document.getElementById('lowCount').innerText = riskCounts.low;
    document.getElementById('moderateCount').innerText = riskCounts.moderate;
    document.getElementById('highCount').innerText = riskCounts.high;
    
    // Update AI Risk Assessment (ML predictions for highest risk zone)
    updateMLRiskAssessment(data.zones);
    
    // Timeline badge removed - using Bootstrap tabs now

    if (data.alerts.length > 0) {
        data.alerts.forEach(a => {
            // Create custom icon for alert markers (consistent with zone markers)
            const alertIcon = L.divIcon({
                className: 'custom-alert-marker',
                html: '<i class="fas fa-location-dot" style="color: #ef4444; font-size: 32px; filter: drop-shadow(0 2px 4px rgba(0,0,0,0.3));"></i>',
                iconSize: [32, 32],
                iconAnchor: [16, 32]
            });
            
            const marker = L.marker([a.lat, a.lon], { icon: alertIcon }).addTo(map);
            marker.bindTooltip(`
                <div style="font-family: 'Poppins', sans-serif; padding: 8px; max-width: 300px;">
                    <strong style="font-size: 1.1em; color: #ef4444;">🚨 Zone ${a.zone_id}</strong><br>
                    <div style="margin-top: 4px; padding: 4px 0; border-top: 1px solid #fee2e2;">
                        Risk Level: <span style="color: #ef4444; font-weight: bold;">🔴 High Risk</span><br>
                        <i class="fas fa-tree"></i> Vegetation: <strong>${a.veg_height_m}m</strong><br>
                        <i class="fas fa-ruler-vertical"></i> Clearance: <strong>${a.clearance_m}m</strong><br>
                        ${a.growth_rate_cm_day ? `<i class="fas fa-chart-line"></i> Growth: <strong>${a.growth_rate_cm_day} cm/day</strong><br>` : ''}
                        ${a.days_until_breach ? `<i class="fas fa-calendar-alt"></i> Breach: <strong>${a.days_until_breach} days</strong><br>` : ''}
                        <small style="color: #64748b;"><i class="fas fa-map-pin"></i> ${a.lat.toFixed(4)}, ${a.lon.toFixed(4)}</small>
                    </div>
                </div>
            `, { sticky: true });
            alertMarkers.push(marker);

            const item = document.createElement('div');
            item.className = 'alert-item';
            item.innerHTML = `
                <strong><i class="fas fa-map-marker-alt"></i> Lat: ${a.lat.toFixed(4)}, Long: ${a.lon.toFixed(4)}</strong><br>
                <i class="fas fa-tree"></i> Vegetation: ${a.veg_height_m}m<br>
                <i class="fas fa-ruler-vertical"></i> Clearance: ${a.clearance_m}m
            `;
            alertItems.appendChild(item);
            
            // Also add to predictor tab's alert list
            const itemPredictor = item.cloneNode(true);
            alertItemsPredictor.appendChild(itemPredictor);

            if ("Notification" in window && Notification.permission === "granted") {
                new Notification(`🔥 ALERT! Zone ${a.zone_id}`, {
                    body: `Vegetation Height: ${a.veg_height_m}m | Clearance: ${a.clearance_m}m`,
                    icon: 'https://em-content.zobj.net/thumbs/120/apple/325/fire_1f525.png'
                });
            }
        });
    } else {
        const noAlertsHtml = '<div style="text-align: center; color: var(--text-dark); padding: 10px;"><i class="fas fa-check-circle" style="color: #56ab2f; font-size: 1.5rem;"></i><br>No active alerts</div>';
        alertItems.innerHTML = noAlertsHtml;
        alertItemsPredictor.innerHTML = noAlertsHtml;
    }
    } catch (error) {
        console.error('❌ Error in updateMapByDate:', error);
        alert('Error loading map data: ' + error.message);
    }
}

function playSimulation() {
    if (isPlaying) return;
    
    // Ensure we have a valid currentDate
    if (!currentDate || dateList.indexOf(currentDate) === -1) {
        currentDate = dateList[0];
    }
    
    isPlaying = true;
    
    // Clear any existing interval
    if (intervalId) {
        clearInterval(intervalId);
        intervalId = null;
    }
    
    intervalId = setInterval(() => {
        // Get current index
        let index = dateList.indexOf(currentDate);
        
        // Safety check - if date not found, use current slider position
        if (index === -1) {
            const slider = document.getElementById('dayRange');
            if (slider) {
                index = parseInt(slider.value) || 0;
            } else {
                index = 0;
            }
        }
        
        // Check if we've reached the end
        if (index >= dateList.length - 1) {
            pauseSimulation();
            return;
        }
        
        // Move to next date
        const nextIndex = index + 1;
        currentDate = dateList[nextIndex];
        
        // Update map with new date
        updateMapByDate(currentDate);
    }, 800);
}

function pauseSimulation() {
    isPlaying = false;
    if (intervalId) {
        clearInterval(intervalId);
        intervalId = null;
    }
}

// Wait for DOM to be fully loaded before setting up event listeners
(async function init() {
    try {
        console.log('🚀 Starting initialization...');
        
        // Wait for DOM to be ready
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', init);
            return;
        }
        
        console.log('📡 Fetching metadata...');
        if ("Notification" in window) Notification.requestPermission();
        
        const metadata = await fetchMetadata();
        console.log('✅ Metadata fetched, dates:', dateList.length);
        
        if (!dateList || dateList.length === 0) {
            console.error('❌ No dates found in metadata!');
            return;
        }
        
        console.log('🗺️ Drawing transmission lines...');
        drawLines(metadata.lines);
        
        console.log('📅 Setting initial date...');
        currentDate = dateList[0];
        console.log('Initial date:', currentDate);
        
        console.log('🗺️ Loading zones for initial date...');
        await updateMapByDate(currentDate);
        console.log('✅ Zones loaded!');

        // Setup slider (retry if not ready)
        function setupSlider() {
            const slider = document.getElementById('dayRange');
            if (slider && dateList.length > 0) {
                slider.max = dateList.length - 1;
                slider.removeEventListener('input', slider._handler);
                slider._handler = function(e) {
                    if (isUpdatingSlider) return;
                    const index = parseInt(e.target.value);
                    if (index >= 0 && index < dateList.length) {
                        pauseSimulation();
                        currentDate = dateList[index];
                        updateMapByDate(currentDate);
                    }
                };
                slider.addEventListener('input', slider._handler);
                console.log('✅ Slider initialized');
            } else {
                setTimeout(setupSlider, 100);
            }
        }
        setupSlider();
        
        console.log('✅ App initialized successfully!');
    } catch (error) {
        console.error('❌ Initialization error:', error);
        alert('Error loading app: ' + error.message);
    }
})();

// Use event delegation for buttons (works even if buttons aren't in DOM yet)
// This MUST be outside the async function so it runs immediately
document.addEventListener('click', function(e) {
    // Play button
    if (e.target.closest('#playBtn') || e.target.id === 'playBtn') {
        e.preventDefault();
        e.stopPropagation();
        console.log('▶️ Play clicked');
        if (!isPlaying) {
            playSimulation();
        }
        return false;
    }
    
    // Pause button  
    if (e.target.closest('#pauseBtn') || e.target.id === 'pauseBtn') {
        e.preventDefault();
        e.stopPropagation();
        console.log('⏸️ Pause clicked');
        pauseSimulation();
        return false;
    }
    
    // Reset button
    if (e.target.closest('#resetBtn') || e.target.id === 'resetBtn') {
        e.preventDefault();
        e.stopPropagation();
        console.log('⏮️ Reset clicked');
        pauseSimulation();
        if (dateList.length > 0) {
            currentDate = dateList[0];
            updateMapByDate(currentDate);
        }
        return false;
    }
});

// California approximate bounds
const CA_BOUNDS = {
    minLat: 32.5,
    maxLat: 42.0,
    minLon: -124.5,
    maxLon: -114.0
};

// City coordinates (lat, lon)
const cityCenters = {
    adelanto: [34.58277, -117.40922],
agouraHills: [34.13639, -118.77453],
alameda: [37.76521, -122.24164],
alhambra: [34.09528, -118.12701],
alisoViejo: [33.56768, -117.72500],
alpine: [32.83532, -116.76675],
altadena: [34.19653, -118.13120],
alturas: [41.48753, -120.54595],
amador: [38.44520, -120.78360],
americanCanyon: [38.18560, -122.26955],
anaheim: [33.8366, -117.9143],
anaheimHills: [33.84119, -117.75865],
anderson: [40.10578, -122.23100],
angelsCamp: [38.06777, -120.53862],
angelusOaks: [34.26750, -116.71500],
antioch: [38.00492, -121.80579],
appleValley: [34.50083, -117.18588],
aptos: [36.97757, -121.90268],
arcadia: [34.13973, -118.03534],
arcata: [40.86697, -124.08251],
arnold: [38.42513, -120.26393],
arroyoGrande: [35.11847, -120.59475],
artesia: [33.84457, -118.08146],
atascadero: [35.48947, -120.67029],
auburn: [38.89660, -121.07688],
avalon: [33.34281, -118.32779],
avilaBeach: [35.14100, -120.62667],
azusa: [34.13066, -117.90687],
bakersfield: [35.3733, -119.0187],
boulderCreek: [37.1269, -122.1211],
bigbar: [40.6552, -122.4194],  // Big Bar Mountain, Trinity County
chulaVista: [32.6401, -117.0842],
fresno: [36.7378, -119.7871],
irvine: [33.6846, -117.8265],
longBeach: [33.7701, -118.1937],
losAngeles: [34.0522, -118.2437],
oakland: [37.8044, -122.2712],
panocheValley: [36.8500, -120.9000],
riverside: [33.9806, -117.3755],
sacramento: [38.5816, -121.4944],
sanDiego: [32.7157, -117.1611],
sanFrancisco: [37.7749, -122.4194],
sanJose: [37.3382, -121.8863],
santaAna: [33.7455, -117.8677],
stockton: [37.9577, -121.2908]
};

// Store transmission tower markers
let transmissionTowerMarkers = [];

document.getElementById('goBtn').addEventListener('click', async () => {
    let lat = parseFloat(document.getElementById('latInput').value);
    let lon = parseFloat(document.getElementById('lonInput').value);
    let cityVal = document.getElementById('citySelect').value;

    let targetLat, targetLon;

    if (cityVal !== "") {
        // Focus on selected city
        const coords = cityCenters[cityVal];
        targetLat = coords[0];
        targetLon = coords[1];
    } else if (!isNaN(lat) && !isNaN(lon)) {
        // Focus on input coordinates
        if (lat < CA_BOUNDS.minLat || lat > CA_BOUNDS.maxLat || lon < CA_BOUNDS.minLon || lon > CA_BOUNDS.maxLon) {
            alert("Data for electric transmission lines is only available in California!");
            return;
        }
        targetLat = lat;
        targetLon = lon;
    } else {
        alert("Please select a city or enter coordinates!");
        return;
    }

    // Smooth zoom/fly to the location
    map.flyTo([targetLat, targetLon], 11, { animate: true, duration: 2.0 });
    
    // Show loading indicator
    const alertItemsElement = document.getElementById('alertItems');
    alertItemsElement.innerHTML = '<div style="text-align: center; padding: 20px;"><i class="fas fa-spinner fa-spin" style="font-size: 24px; color: #6366f1;"></i><br><small>Scanning transmission towers...</small></div>';
    
    // Fetch transmission tower data with risk metrics
    try {
        const response = await fetch(`/api/detect_vegetation_risk?lat=${targetLat}&lon=${targetLon}&radius=15`);
        const data = await response.json();
        
        console.log('API Response:', data);
        console.log('Has analysis_summary:', !!data.analysis_summary);
        
        // Clear old transmission tower markers
        transmissionTowerMarkers.forEach(marker => map.removeLayer(marker));
        transmissionTowerMarkers = [];
        
        // Draw transmission towers on map with risk metrics
        if (data.towers && data.towers.length > 0) {
            console.log(`Found ${data.towers.length} moderate/high risk towers:`, data.towers);
            data.towers.forEach(tower => {
                // Determine marker color based on risk level
                let markerColor, markerLabel;
                if (tower.risk_level === 'low') {
                    markerColor = '#10b981';
                    markerLabel = '⚡';
                } else if (tower.risk_level === 'moderate') {
                    markerColor = '#f59e0b';
                    markerLabel = '⚡';
                } else {
                    markerColor = '#ef4444';
                    markerLabel = '⚡';
                }

                const towerIcon = L.divIcon({
                    className: 'clean-marker',
                    html: `<div style="
                        background-color: ${markerColor}; 
                        width: 28px; 
                        height: 28px; 
                        border-radius: 50%; 
                        border: 3px solid white; 
                        box-shadow: 0 3px 8px rgba(0,0,0,0.3);
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        color: white;
                        font-weight: bold;
                        font-size: 14px;
                    ">${markerLabel}</div>`,
                    iconSize: [28, 28],
                    iconAnchor: [14, 14]
                });

                const marker = L.marker([tower.latitude, tower.longitude], { icon: towerIcon }).addTo(map);
                
                // Enhanced tooltip with tower metrics
                const riskLabels = {
                    'low': '<span style="color: #10b981; font-weight: bold;">🟢 Low Risk</span>',
                    'moderate': '<span style="color: #f59e0b; font-weight: bold;">🟡 Moderate Risk</span>',
                    'high': '<span style="color: #ef4444; font-weight: bold;">🔴 High Risk</span>'
                };
                
                const alertBadge = tower.alert ? '<span style="background: #ef4444; color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.7em;">🚨 ALERT</span>' : '';
                
                // Normalized tooltip showing consistent data across all risk levels
                marker.bindTooltip(`
                    <div style="font-family: 'Poppins', sans-serif; padding: 8px; max-width: 280px;">
                        <strong style="font-size: 1em; color: #1e293b;">⚡ ${tower.point_id}</strong> ${alertBadge}<br>
                        <div style="margin-top: 4px; padding: 4px 0; border-top: 1px solid #e2e8f0;">
                            ${riskLabels[tower.risk_level]}<br>
                            <i class="fas fa-tree"></i> Vegetation Height: <strong>${parseFloat(tower.veg_height_m).toFixed(2)}m</strong><br>
                            <i class="fas fa-arrows-alt-v"></i> Line Height: <strong>${tower.line_height_m}m</strong><br>
                            <i class="fas fa-ruler-vertical"></i> Clearance: <strong>${tower.clearance_m}m</strong><br>
                            <i class="fas fa-bolt"></i> kV Rating: <strong>${tower.kv_rating}kV</strong><br>
                            <i class="fas fa-exclamation-triangle"></i> Powerline Status: <strong>${tower.risk_level.charAt(0).toUpperCase() + tower.risk_level.slice(1)} Risk</strong>
                        </div>
                    </div>
                `, { sticky: true });
                
                transmissionTowerMarkers.push(marker);
            });
        }
        
        // Draw low risk points as small green icons
        if (data.low_risk_points && data.low_risk_points.length > 0) {
            console.log(`Found ${data.low_risk_points.length} low risk points:`, data.low_risk_points);
            data.low_risk_points.forEach(point => {
                const lowRiskIcon = L.divIcon({
                    className: 'clean-marker',
                    html: `<div style="
                        background-color: #10b981; 
                        width: 18px; 
                        height: 18px; 
                        border-radius: 50%; 
                        border: 2px solid white; 
                        box-shadow: 0 2px 4px rgba(0,0,0,0.2);
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        color: white;
                        font-weight: bold;
                        font-size: 10px;
                    ">✓</div>`,
                    iconSize: [18, 18],
                    iconAnchor: [9, 9]
                });

                const marker = L.marker([point.latitude, point.longitude], { icon: lowRiskIcon }).addTo(map);
                
                // Tooltip with point metrics
                marker.bindTooltip(`
                    <div style="font-family: 'Poppins', sans-serif; padding: 8px; max-width: 280px;">
                        <strong style="font-size: 1em; color: #10b981;">🟢 Low Risk - ${point.point_id}</strong><br>
                        <div style="margin-top: 4px; padding: 4px 0; border-top: 1px solid #e2e8f0;">
                            <i class="fas fa-tree"></i> Vegetation Height: <strong>${parseFloat(point.veg_height_m).toFixed(2)}m</strong><br>
                            <i class="fas fa-arrows-alt-v"></i> Line Height: <strong>${point.line_height_m}m</strong><br>
                            <i class="fas fa-ruler-vertical"></i> Clearance: <strong>${point.clearance_m}m</strong><br>
                            <i class="fas fa-bolt"></i> kV Rating: <strong>${point.kv_rating}kV</strong><br>
                            <i class="fas fa-check-circle"></i> Powerline Status: <strong style="color: #10b981;">Low Risk</strong>
                        </div>
                    </div>
                `, { sticky: true });
                
                transmissionTowerMarkers.push(marker);
            });
        }
        
        // Update info panel with statistics
        if (data.towers && data.towers.length > 0) {
            const stats = data.statistics;
            const sourceNote = data.data_source === 'simulated' ? '<br><em style="font-size: 0.55rem; color: #94a3b8;">Demo Mode - Production connects to utility SCADA systems</em>' : '';
            alertItemsElement.innerHTML = `
                <div style="padding: 10px; background: rgba(99, 102, 241, 0.1); border-radius: 8px; border-left: 3px solid #6366f1;">
                    <strong style="color: #4f46e5;"><i class="fas fa-broadcast-tower"></i> ${data.towers_found} Transmission Towers Monitored</strong><br>
                    <div style="margin-top: 8px; padding: 6px; background: rgba(255,255,255,0.5); border-radius: 4px;">
                        <small style="color: #ef4444; font-weight: bold;">🔴 ${stats.critical_alerts} Critical Alerts</small><br>
                        <small style="color: #64748b;">
                            🟢 Low: ${stats.risk_distribution.low} | 
                            🟡 Moderate: ${stats.risk_distribution.moderate} | 
                            🔴 High: ${stats.risk_distribution.high}
                        </small><br>
                        <small style="color: #64748b;">Avg Vegetation: ${stats.avg_vegetation_height}m</small>
                    </div>
                    <small style="color: #64748b; margin-top: 4px; display: block;">Location: ${targetLat.toFixed(4)}, ${targetLon.toFixed(4)}<br>
                    Radius: ${data.radius_km}km${sourceNote}</small>
                </div>
            `;
            
            // Update RIGHT PANEL Live Metrics to show tower data
            document.getElementById('alertCount').innerText = stats.critical_alerts;
            document.getElementById('zoneCount').innerText = stats.total_towers;
            document.getElementById('avgHeight').innerText = stats.avg_vegetation_height + 'm';
            
            // Update Risk Levels in right panel
            document.getElementById('lowCount').innerText = stats.risk_distribution.low;
            document.getElementById('moderateCount').innerText = stats.risk_distribution.moderate;
            document.getElementById('highCount').innerText = stats.risk_distribution.high;
            
            // Update ML Risk Assessment for highest risk tower
            const highRiskTowers = data.towers.filter(t => t.risk_level === 'high' || t.alert);
            if (highRiskTowers.length > 0) {
                const worstTower = highRiskTowers.reduce((prev, curr) => 
                    curr.clearance_m < prev.clearance_m ? curr : prev
                );
                
                document.getElementById('growthRateDisplay').innerText = `${worstTower.growth_rate_cm_day} cm/day`;
                
                if (worstTower.days_until_breach <= 0) {
                    document.getElementById('breachPrediction').innerHTML = '<span style="color: #ef4444; font-weight: bold;">🔴 BREACHED</span>';
                } else if (worstTower.days_until_breach < 999) {
                    document.getElementById('breachPrediction').innerText = `${worstTower.days_until_breach} days`;
                } else {
                    document.getElementById('breachPrediction').innerText = 'Safe';
                }
                
                // Call ML prediction for this tower
                try {
                    const mlRes = await fetch(`/api/ml_predict?veg_height=${worstTower.veg_height_m}&clearance=${worstTower.clearance_m}&temperature=25&humidity=50&wind_speed=5&days_since_rain=7&latitude=${worstTower.latitude}&longitude=${worstTower.longitude}`);
                    const mlData = await mlRes.json();
                    
                    document.getElementById('mlRiskLevel').innerText = mlData.risk_level || worstTower.risk_level;
                    document.getElementById('mlRiskScore').innerText = (mlData.risk_score ? (mlData.risk_score * 100).toFixed(1) + '%' : '-');
                    document.getElementById('mlConfidence').innerText = (mlData.confidence ? (mlData.confidence * 100).toFixed(0) + '%' : '-');
                } catch (mlError) {
                    console.error('Error fetching ML prediction for tower:', mlError);
                }
            }
        } else {
            alertItemsElement.innerHTML = '<div style="text-align: center; color: #64748b; padding: 10px;"><i class="fas fa-info-circle"></i><br>No transmission towers found in this area</div>';
        }
        
        // Show analysis summary modal (regardless of whether towers were found)
        if (data.analysis_summary) {
            console.log('Analysis summary data:', data.analysis_summary);
            const summary = data.analysis_summary;
            document.getElementById('modalLinesProcessed').innerText = summary.lines_processed || 0;
            document.getElementById('modalLinesInRadius').innerText = summary.lines_in_radius || 0;
            document.getElementById('modalCoordinatesAnalyzed').innerText = summary.coordinates_analyzed || 0;
            document.getElementById('modalMediumAlerts').innerText = summary.medium_alerts || 0;
            document.getElementById('modalHighAlerts').innerText = summary.high_alerts || 0;
            
            // Show the modal
            try {
                const modalElement = document.getElementById('analysisModal');
                console.log('Modal element found:', modalElement);
                const modal = new bootstrap.Modal(modalElement);
                console.log('Showing modal...');
                modal.show();
            } catch (modalError) {
                console.error('Error showing modal:', modalError);
            }
        } else {
            console.log('No analysis_summary in response:', data);
        }
    } catch (error) {
        console.error('Error fetching transmission towers:', error);
        alertItemsElement.innerHTML = '<div style="text-align: center; color: #ef4444; padding: 10px;"><i class="fas fa-exclamation-triangle"></i><br>Error loading transmission infrastructure</div>';
    }
});


// ========== TOGGLE PANELS ==========
// Bootstrap tabs handle tab switching automatically
const toggleBtn = document.getElementById('togglePanelsBtn');
const mergedPanel = document.getElementById('mergedPanel');
const metricsPanel = document.getElementById('metricsPanel');
let panelsVisible = true;

toggleBtn.addEventListener('click', () => {
    panelsVisible = !panelsVisible;
    if (panelsVisible) {
        mergedPanel.classList.remove('hidden');
        metricsPanel.classList.remove('hidden');
    } else {
        mergedPanel.classList.add('hidden');
        metricsPanel.classList.add('hidden');
    }
});

// ========== NOTIFY AUTHORITY BUTTON ==========
document.getElementById('notifyAuthorityBtn').addEventListener('click', async () => {
    const alertCount = parseInt(document.getElementById('alertCount').innerText);
    if (alertCount === 0) {
        alert('No active alerts to report.');
        return;
    }
    
    const confirmed = confirm(`Send notification for ${alertCount} critical alert(s) to authorities?`);
    if (confirmed) {
        try {
            const response = await fetch('/api/notify', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    date: currentDate,
                    alert_count: alertCount
                })
            });
            
            if (response.ok) {
                alert('✅ Authorities have been notified!');
            } else {
                alert('⚠️  Failed to send notification. Please try again.');
            }
        } catch (error) {
            console.error('Error sending notification:', error);
            alert('⚠️  Error sending notification. Please check your connection.');
        }
    }
});

// ========== INITIALIZE: Load zones on page load ==========
// This is handled by the init() function above, so we don't need duplicate initialization

// ========== TAB SWITCHING: Reset to default zones when switching to Time Predictor ==========
const tab2Btn = document.getElementById('tab2-btn');
if (tab2Btn) {
    tab2Btn.addEventListener('click', async () => {
        // Clear any transmission tower markers
        transmissionTowerMarkers.forEach(marker => map.removeLayer(marker));
        transmissionTowerMarkers = [];
        
        // Reload default zones
        if (dateList.length > 0) {
            currentDate = dateList[0];
            await updateMapByDate(currentDate);
        }
        
        // Reset the info text in Tab 1
        const alertItems = document.getElementById('alertItems');
        if (alertItems) {
            alertItems.innerHTML = 'Select a city or enter coordinates to scan transmission towers with vegetation risk metrics';
        }
        
        // Controls are already set up via event delegation, no need to re-setup
        
        console.log('✅ Reset to default zones');
    });
}
