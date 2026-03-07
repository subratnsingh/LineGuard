import json
from typing import List, Tuple
from shapely.geometry import shape, Point, LineString
from shapely.ops import transform
import pyproj
from math import radians, cos, sin, asin, sqrt


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great circle distance between two points in miles.
    """
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    miles = 3959 * c
    return miles


def is_within_radius(line_coords: List[Tuple[float, float]], 
                     center_lat: float, 
                     center_lon: float, 
                     radius_miles: float) -> bool:
    """
    Check if any part of a line is within the specified radius.
    """
    for lon, lat in line_coords:
        if haversine_distance(center_lat, center_lon, lat, lon) <= radius_miles:
            return True
    return False


def sample_points_along_line(line: LineString, 
                             interval_feet: float,
                             center_lat: float,
                             center_lon: float,
                             radius_miles: float) -> List[Tuple[float, float]]:
    """
    Sample points along a line at specified intervals, filtering by radius.
    FIXED: Uses modern pyproj Transformer API
    """
    # Create projection transformers using modern pyproj API
    wgs84 = pyproj.CRS('EPSG:4326')
    # Albers Equal Area Conic for US
    aea = pyproj.CRS('+proj=aea +lat_1=29.5 +lat_2=45.5 +lat_0=37.5 +lon_0=-96 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs')
    
    # Create transformers
    transformer_to_aea = pyproj.Transformer.from_crs(wgs84, aea, always_xy=True)
    transformer_to_wgs84 = pyproj.Transformer.from_crs(aea, wgs84, always_xy=True)
    
    # Transform line to projected coordinates
    line_projected = transform(transformer_to_aea.transform, line)
    
    # Convert feet to meters
    interval_meters = interval_feet * 0.3048
    
    # Sample points along the line
    sampled_points = []
    line_length = line_projected.length
    
    # Sample at regular intervals
    distance = 0
    while distance <= line_length:
        point_projected = line_projected.interpolate(distance)
        
        # Transform back to WGS84
        lon, lat = transformer_to_wgs84.transform(point_projected.x, point_projected.y)
        
        # Check if point is within radius
        if haversine_distance(center_lat, center_lon, lat, lon) <= radius_miles:
            sampled_points.append((lat, lon))
        
        distance += interval_meters
    
    return sampled_points



def extract_powerline_points(geojson_file: str,
                            center_lat: float,
                            center_lon: float,
                            radius_miles: float = 10,
                            interval_feet: float = 3000,
                            verbose: bool = True,
                            return_stats: bool = False):
    """
    Extract points along power transmission lines within a radius.
    
    Args:
        return_stats: If True, returns (points, stats) tuple instead of just points
    
    Returns:
        If return_stats=False: List of dicts with format:
        {
            'lat': float,
            'lon': float,
            'properties': dict  # includes kV rating and other feature properties
        }
        If return_stats=True: Tuple of (points, stats) where stats contains:
        {
            'lines_processed': int,
            'lines_in_radius': int,
            'total_points': int
        }
    """
    if verbose:
        print(f"Loading GeoJSON from: {geojson_file}")
    
    # Load GeoJSON data
    with open(geojson_file, 'r') as f:
        geojson_data = json.load(f)
    
    features = geojson_data.get('features', [])
    if verbose:
        print(f"Total features: {len(features)}")
        print(f"Center: ({center_lat}, {center_lon})")
        print(f"Radius: {radius_miles} miles")
        print(f"Sampling interval: {interval_feet} feet")
        print()
    
    all_points = []
    lines_processed = 0
    lines_in_radius = 0
    
    # Process each feature
    for idx, feature in enumerate(features):
        if 'geometry' not in feature or not feature['geometry']:
            continue
        
        try:
            geometry = shape(feature['geometry'])
        except Exception as e:
            if verbose:
                print(f"Warning: Could not parse feature {idx}: {e}")
            continue
        
        # Handle both LineString and MultiLineString
        if geometry.geom_type == 'LineString':
            lines = [geometry]
        elif geometry.geom_type == 'MultiLineString':
            lines = list(geometry.geoms)
        else:
            continue
        
        # Extract properties (including kV rating if present)
        properties = feature.get('properties', {})
        
        # Process each line
        for line in lines:
            lines_processed += 1
            coords = list(line.coords)
            
            # Quick check: is any part of the line within radius?
            if is_within_radius(coords, center_lat, center_lon, radius_miles):
                lines_in_radius += 1
                
                # Sample points along this line
                points = sample_points_along_line(line, interval_feet, 
                                                 center_lat, center_lon, 
                                                 radius_miles)
                # Add properties to each point
                for lat, lon in points:
                    all_points.append({
                        'lat': lat,
                        'lon': lon,
                        'properties': properties  # includes kV rating if present
                    })
                
                if verbose and lines_in_radius % 10 == 0:
                    print(f"Processed {lines_in_radius} lines in radius, {len(all_points)} points so far...")
    
    if verbose:
        print()
        print(f"=== RESULTS ===")
        print(f"Total lines processed: {lines_processed}")
        print(f"Lines within radius: {lines_in_radius}")
        print(f"Total points extracted: {len(all_points)}")
    
    if return_stats:
        stats = {
            'lines_processed': lines_processed,
            'lines_in_radius': lines_in_radius,
            'total_points': len(all_points)
        }
        return all_points, stats
    else:
        return all_points


def main():
    """Example usage with Fort Dick, CA coordinates"""
    geojson_file = 'power_transmission_lines.geojson'
    center_lat = 41.868074  # Fort Dick, CA
    center_lon = -124.152736
    radius_miles = 50
    interval_feet = 3
    
    print("=" * 70)
    print("EXTRACTING POWERLINE POINTS")
    print("=" * 70)
    print()
    
    points = extract_powerline_points(
        geojson_file,
        center_lat,
        center_lon,
        radius_miles,
        interval_feet,
        verbose=True
    )
    
    if len(points) == 0:
        print()
        print("⚠ WARNING: No points extracted!")
        print("This could mean:")
        print("  1. GeoJSON file not found or empty")
        print("  2. No power lines within the specified radius")
        print("  3. Lines exist but have no geometry data")
        return
    
    print()
    print("=" * 70)
    print("SAMPLE OUTPUT (first 20 points with properties):")
    print("=" * 70)
    all_property_keys = set()
    kv_ratings = set()
    for i, point in enumerate(points[:20]):
        lat = point['lat']
        lon = point['lon']
        properties = point['properties']
        all_property_keys.update(properties.keys())
        kv = properties.get('KV') or properties.get('kV') or properties.get('VOLTAGE') or properties.get('voltage')
        if kv:
            kv_ratings.add(str(kv))
        print(f"\n{i+1:3d}. Lat: {lat:10.6f}, Lon: {lon:11.6f}")
        print(f"     Properties: {properties}")
        if kv:
            print(f"     ⚡ kV Rating: {kv}")
    if len(points) > 20:
        print(f"\n... and {len(points) - 20} more points")
    print()
    print("=" * 70)
    print("PROPERTY SUMMARY:")
    print("=" * 70)
    print(f"Total points extracted: {len(points)}")
    print(f"Unique property keys found: {sorted(all_property_keys)}")
    print(f"Unique kV ratings found: {sorted(kv_ratings) if kv_ratings else 'None found'}")
    # Save to JSON file with properties
    output_file = 'powerline_points_output.json'
    print()
    print(f"Saving results to: {output_file}")
    with open(output_file, 'w') as f:
        json.dump({
            'metadata': {
                'center_lat': center_lat,
                'center_lon': center_lon,
                'radius_miles': radius_miles,
                'interval_feet': interval_feet,
                'total_points': len(points),
                'property_keys': list(all_property_keys),
                'kv_ratings_found': list(kv_ratings)
            },
            'points': points
        }, f, indent=2)
    print(f"✓ Saved {len(points)} points with properties to {output_file}")
    # Also save as CSV with kV rating
    csv_file = 'powerline_points_output.csv'
    with open(csv_file, 'w') as f:
        f.write('latitude,longitude,kv_rating,properties\n')
        for point in points:
            lat = point['lat']
            lon = point['lon']
            props = point['properties']
            kv = props.get('KV') or props.get('kV') or props.get('VOLTAGE') or props.get('voltage') or 'Unknown'
            props_str = json.dumps(props).replace(',', ';')
            f.write(f'{lat},{lon},{kv},{props_str}\n')
    print(f"✓ Saved {len(points)} points with kV ratings to {csv_file}")