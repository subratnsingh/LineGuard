"""
ICESat-2 Vegetation Height with CSV Caching

Performance improvement: Caches API results to avoid redundant calls.
- Stores results in CSV file
- Checks cache before making API calls
- Returns cached data if available within tolerance

Note: SlideRule returns GeoDataFrame with 'geometry' column.
      Script automatically extracts lat/lon from geometry.

Requires: pip install sliderule pandas --break-system-packages
"""

import pandas as pd
import numpy as np
from typing import Tuple, Optional, Dict
from datetime import datetime, timedelta
import os


# Global cache settings
CACHE_FILE = "icesat2_cache.csv"
CACHE_DISTANCE_TOLERANCE_KM = 0.5  # Match if within 500 meters


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate distance between two points in kilometers.
    
    Args:
        lat1, lon1: First point
        lat2, lon2: Second point
    
    Returns:
        Distance in kilometers
    """
    from math import radians, cos, sin, asin, sqrt
    
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    km = 6371 * c  # Radius of earth in kilometers
    return km


def load_cache(cache_file: str = CACHE_FILE) -> pd.DataFrame:
    """
    Load cache from CSV file.
    
    Args:
        cache_file: Path to cache CSV file
    
    Returns:
        DataFrame with cached data, or empty DataFrame if file doesn't exist
    """
    if os.path.exists(cache_file):
        try:
            df = pd.read_csv(cache_file)
            print(f"✓ Loaded cache: {len(df)} records from {cache_file}")
            return df
        except Exception as e:
            print(f"Warning: Could not load cache: {e}")
            return pd.DataFrame()
    else:
        print(f"Cache file not found: {cache_file} (will be created)")
        return pd.DataFrame()


def save_to_cache(data: Dict, cache_file: str = CACHE_FILE, no_data: bool = False):
    """
    Save a single measurement to cache.
    
    Args:
        data: Dictionary with measurement data (or query location if no_data=True)
        cache_file: Path to cache CSV file
        no_data: If True, saves as "no data available" entry
    """
    try:
        # Load existing cache (suppress the "loaded cache" message)
        if os.path.exists(cache_file):
            try:
                cache_df = pd.read_csv(cache_file)
            except Exception as e:
                print(f"Warning: Could not load existing cache: {e}")
                cache_df = pd.DataFrame()
        else:
            cache_df = pd.DataFrame()
        
        # Create new row
        if no_data:
            # Save "no data" entry with NaN for data fields
            new_row = pd.DataFrame([{
                'latitude': data['latitude'],
                'longitude': data['longitude'],
                'vegetation_height_m': np.nan,
                'terrain_height_m': np.nan,
                'canopy_height_m': np.nan,
                'measurement_date': None,
                'distance_from_query_km': 0,
                'cached_at': datetime.now().isoformat(),
                'has_data': False  # Flag to indicate no data
            }])
        else:
            # Save successful measurement
            new_row = pd.DataFrame([{
                'latitude': data['latitude'],
                'longitude': data['longitude'],
                'vegetation_height_m': data['vegetation_height_m'],
                'terrain_height_m': data.get('ground_elevation_m', np.nan),
                'canopy_height_m': data.get('canopy_elevation_m', np.nan),
                'measurement_date': data.get('date', datetime.now().isoformat()),
                'distance_from_query_km': data.get('distance_km', 0),
                'cached_at': datetime.now().isoformat(),
                'has_data': True  # Flag to indicate valid data
            }])
        
        # Append to cache
        if cache_df.empty:
            cache_df = new_row
        else:
            cache_df = pd.concat([cache_df, new_row], ignore_index=True)
        
        # Save to file
        cache_df.to_csv(cache_file, index=False)
        
        if no_data:
            print(f"✓ Saved 'no data' entry to cache: {cache_file} (total: {len(cache_df)} records)")
        else:
            print(f"✓ Saved to cache: {cache_file} (total: {len(cache_df)} records)")
        
        # Verify it was saved
        if os.path.exists(cache_file):
            file_size = os.path.getsize(cache_file)
            print(f"  Cache file size: {file_size} bytes")
        else:
            print(f"  WARNING: Cache file was not created!")
        
        return True
        
    except Exception as e:
        print(f"ERROR saving to cache: {e}")
        import traceback
        traceback.print_exc()
        return False


def find_in_cache(lat: float, lon: float, 
                  cache_file: str = CACHE_FILE,
                  tolerance_km: float = CACHE_DISTANCE_TOLERANCE_KM) -> Optional[Dict]:
    """
    Search cache for measurements near the given location.
    
    Args:
        lat: Latitude to search for
        lon: Longitude to search for
        cache_file: Path to cache CSV file
        tolerance_km: Maximum distance to consider a match (km)
    
    Returns:
        Dictionary with cached data if found, None otherwise
    """
    cache_df = load_cache(cache_file)
    
    if cache_df.empty:
        print(f"Cache is empty, need to fetch from API")
        return None
    
    # Calculate distance to all cached points
    cache_df['distance_to_query'] = cache_df.apply(
        lambda row: haversine_distance(lat, lon, row['latitude'], row['longitude']),
        axis=1
    )
    
    # Find closest point within tolerance
    within_tolerance = cache_df[cache_df['distance_to_query'] <= tolerance_km]
    
    if within_tolerance.empty:
        print(f"No cached data within {tolerance_km} km, need to fetch from API")
        return None
    
    # Return the closest match
    closest = within_tolerance.loc[within_tolerance['distance_to_query'].idxmin()]
    
    # Check if this is a "no data" entry
    has_data = closest.get('has_data', True)  # Default to True for old cache entries
    if pd.isna(has_data):
        has_data = not pd.isna(closest['vegetation_height_m'])
    
    if not has_data:
        # This location was checked before and has no data
        print(f"✓ Found in cache: No ICESat-2 data available at this location")
        print(f"  (Previously checked, {closest['distance_to_query']:.3f} km from query)")
        return {
            'latitude': float(closest['latitude']),
            'longitude': float(closest['longitude']),
            'has_data': False,
            'distance_km': float(closest['distance_to_query']),
            'data_source': 'Cache (No Data)',
            'cached': True
        }
    
    # Return successful cached data
    result = {
        'latitude': float(closest['latitude']),
        'longitude': float(closest['longitude']),
        'vegetation_height_m': float(closest['vegetation_height_m']),
        'ground_elevation_m': float(closest['terrain_height_m']) if not pd.isna(closest['terrain_height_m']) else None,
        'canopy_elevation_m': float(closest['canopy_height_m']) if not pd.isna(closest['canopy_height_m']) else None,
        'date': str(closest['measurement_date']),
        'distance_km': float(closest['distance_to_query']),
        'has_data': True,
        'data_source': 'Cache',
        'cached': True
    }
    
    print(f"✓ Found in cache: {result['distance_km']:.3f} km from query point")
    return result


def get_icesat2_with_cache(lat: float, lon: float,
                           buffer_km: float = 5.0,
                           date_start: Optional[str] = None,
                           date_end: Optional[str] = None,
                           cache_file: str = CACHE_FILE,
                           use_cache: bool = True) -> Optional[Dict]:
    """
    Get ICESat-2 vegetation height with caching.
    
    Checks cache first, only makes API call if not found.
    
    Args:
        lat: Latitude
        lon: Longitude
        buffer_km: Search radius for API call (km)
        date_start: Start date for API call (YYYY-MM-DD)
        date_end: End date for API call (YYYY-MM-DD)
        cache_file: Path to cache CSV file
        use_cache: Whether to use cache (set False to force API call)
    
    Returns:
        Dictionary with vegetation height data
    """
    # Step 1: Check cache first (if enabled)
    if use_cache:
        cached_result = find_in_cache(lat, lon, cache_file)
        if cached_result is not None:
            # Check if this is a "no data" entry
            if cached_result.get('has_data') == False:
                # Return None for consistency with API behavior
                return None
            # Otherwise return the cached data
            return cached_result
    
    # Step 2: Not in cache, fetch from API
    print(f"Fetching from ICESat-2 API...")
    
    try:
        from sliderule import icesat2
    except ImportError:
        print("Error: sliderule not installed")
        print("Install with: pip install sliderule --break-system-packages")
        return None
    
    # Initialize SlideRule
    icesat2.init("slideruleearth.io", verbose=False)
    
    # Default date range
    if date_end is None:
        date_end = datetime.now().strftime('%Y-%m-%d')
    if date_start is None:
        date_start = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    
    # Convert buffer to degrees
    buffer_deg = buffer_km / 111.0
    
    # Define region
    region = [
        {"lon": lon - buffer_deg, "lat": lat - buffer_deg},
        {"lon": lon + buffer_deg, "lat": lat - buffer_deg},
        {"lon": lon + buffer_deg, "lat": lat + buffer_deg},
        {"lon": lon - buffer_deg, "lat": lat + buffer_deg},
        {"lon": lon - buffer_deg, "lat": lat - buffer_deg},
    ]
    
    # Request parameters
    parms = {
        "poly": region,
        "t0": date_start,
        "t1": date_end,
        "cnf": 4,
        "ats": 10.0,
        "cnt": 5,
        "len": 40.0,
        "res": 20.0,
    }
    
    try:
        # Get ATL08 data
        atl08 = icesat2.atl08p(parms)
        
        if atl08.empty:
            #print("No ICESat-2 data found for this location")
            # Save "no data" entry to cache to avoid future API calls
            no_data_entry = {'latitude': lat, 'longitude': lon}
            save_to_cache(no_data_entry, cache_file, no_data=True)
            return None
        
        #print(f"Received {len(atl08)} measurements")
        
        # Find terrain and canopy fields
        terrain_field = None
        canopy_field = None
        
        for field in ['h_te_median', 'h_te_mean', 'h_te_best_fit']:
            if field in atl08.columns:
                terrain_field = field
                break
        
        for field in ['h_canopy', 'h_max_canopy', 'h_mean_canopy']:
            if field in atl08.columns:
                canopy_field = field
                break
        
        if not terrain_field or not canopy_field:
            print(f"Error: Missing required fields")
            # Save "no data" entry to cache
            no_data_entry = {'latitude': lat, 'longitude': lon}
            save_to_cache(no_data_entry, cache_file, no_data=True)
            return None
        
        # Remove fill values
        original_size = len(atl08)
        atl08 = atl08[
            (atl08[canopy_field].abs() < 1e10) &
            (atl08[terrain_field].abs() < 1e10) &
            (atl08[canopy_field] > -500) &
            (atl08[canopy_field] < 9000) &
            (atl08[terrain_field] > -500) &
            (atl08[terrain_field] < 9000)
        ].copy()
        
        removed = original_size - len(atl08)
        #if removed > 0:
        #    print(f"Removed {removed} fill/invalid values")
        
        if atl08.empty:
            print("No valid data after filtering")
            # Save "no data" entry to cache
            no_data_entry = {'latitude': lat, 'longitude': lon}
            save_to_cache(no_data_entry, cache_file, no_data=True)
            return None
        
        # Extract latitude and longitude from geometry column
        # SlideRule returns GeoDataFrame with geometry column
        if 'geometry' in atl08.columns:
            atl08['latitude'] = atl08.geometry.y
            atl08['longitude'] = atl08.geometry.x
        elif 'latitude' not in atl08.columns or 'longitude' not in atl08.columns:
            print("Error: No latitude/longitude or geometry column found")
            # Save "no data" entry to cache
            no_data_entry = {'latitude': lat, 'longitude': lon}
            save_to_cache(no_data_entry, cache_file, no_data=True)
            return None
        
        # Calculate vegetation height
        atl08['vegetation_height_m'] = atl08[canopy_field] - atl08[terrain_field]
        atl08['ground_elevation_m'] = atl08[terrain_field]
        atl08['canopy_elevation_m'] = atl08[canopy_field]
        
        # Filter unrealistic vegetation heights
        atl08 = atl08[
            (atl08['vegetation_height_m'] >= 1) &
            (atl08['vegetation_height_m'] <= 100)
        ].copy()
        
        if atl08.empty:
            #print("No valid vegetation heights")
            # Save "no data" entry to cache
            no_data_entry = {'latitude': lat, 'longitude': lon}
            save_to_cache(no_data_entry, cache_file, no_data=True)
            return None
        
        # Calculate distance from query point using haversine
        atl08['distance_km'] = atl08.apply(
            lambda row: haversine_distance(lat, lon, row['latitude'], row['longitude']),
            axis=1
        )
        
        # Get nearest measurement
        atl08 = atl08.sort_values('distance_km')
        nearest = atl08.iloc[0]
        
        result = {
            'latitude': float(nearest['latitude']),
            'longitude': float(nearest['longitude']),
            'vegetation_height_m': float(nearest['vegetation_height_m']),
            'ground_elevation_m': float(nearest['ground_elevation_m']),
            'canopy_elevation_m': float(nearest['canopy_elevation_m']),
            'date': str(nearest.get('time', 'Unknown')),
            'distance_km': float(nearest['distance_km']),
            'data_source': 'ICESat-2 API',
            'cached': False
        }
        
        #print(f"✓ Found measurement {result['distance_km']:.3f} km from query")
        
        # Debug: Show what we're about to save
        #print(f"\nSaving to cache:")
        #print(f"  Location: ({result['latitude']:.6f}, {result['longitude']:.6f})")
        #print(f"  Vegetation: {result['vegetation_height_m']:.2f} m")
        #print(f"  Cache file: {cache_file}")
        
        # Save to cache
        save_success = save_to_cache(result, cache_file)
        
        if not save_success:
            print("WARNING: Failed to save to cache, but returning result anyway")
        
        return result
        
    except Exception as e:
        print(f"Error fetching ICESat-2 data: {e}")
        import traceback
        traceback.print_exc()
        return None


def batch_get_vegetation_heights(locations: list,
                                 cache_file: str = CACHE_FILE,
                                 buffer_km: float = 5.0) -> pd.DataFrame:
    """
    Get vegetation heights for multiple locations with caching.
    
    Args:
        locations: List of (lat, lon) tuples
        cache_file: Path to cache CSV file
        buffer_km: Search radius for API calls
    
    Returns:
        DataFrame with results for all locations
    """
    results = []
    
    print(f"Processing {len(locations)} locations...")
    print("=" * 70)
    
    for i, (lat, lon) in enumerate(locations):
        print(f"\nLocation {i+1}/{len(locations)}: ({lat:.6f}, {lon:.6f})")
        
        result = get_icesat2_with_cache(lat, lon, buffer_km, cache_file=cache_file)
        
        if result:
            results.append(result)
        else:
            print(f"  No data available")
    
    print("\n" + "=" * 70)
    print(f"Completed: {len(results)}/{len(locations)} locations")
    
    if results:
        df = pd.DataFrame(results)
        return df
    else:
        return pd.DataFrame()


def clear_cache(cache_file: str = CACHE_FILE):
    """
    Clear the cache file.
    
    Args:
        cache_file: Path to cache CSV file
    """
    if os.path.exists(cache_file):
        os.remove(cache_file)
        print(f"✓ Cache cleared: {cache_file}")
    else:
        print(f"Cache file not found: {cache_file}")


def cache_statistics(cache_file: str = CACHE_FILE):
    """
    Display cache statistics.
    
    Args:
        cache_file: Path to cache CSV file
    """
    cache_df = load_cache(cache_file)
    
    if cache_df.empty:
        print("Cache is empty")
        return
    
    print("\n" + "=" * 70)
    print("CACHE STATISTICS")
    print("=" * 70)
    print(f"Total records: {len(cache_df)}")
    print(f"Cache file: {cache_file}")
    print(f"File size: {os.path.getsize(cache_file) / 1024:.2f} KB")
    
    print(f"\nVegetation height statistics:")
    print(f"  Mean: {cache_df['vegetation_height_m'].mean():.2f} m")
    print(f"  Median: {cache_df['vegetation_height_m'].median():.2f} m")
    print(f"  Min: {cache_df['vegetation_height_m'].min():.2f} m")
    print(f"  Max: {cache_df['vegetation_height_m'].max():.2f} m")
    
    print(f"\nSpatial coverage:")
    print(f"  Latitude range: {cache_df['latitude'].min():.3f} to {cache_df['latitude'].max():.3f}")
    print(f"  Longitude range: {cache_df['longitude'].min():.3f} to {cache_df['longitude'].max():.3f}")
    
    if 'cached_at' in cache_df.columns:
        cache_df['cached_at'] = pd.to_datetime(cache_df['cached_at'])
        print(f"\nTemporal coverage:")
        print(f"  Oldest entry: {cache_df['cached_at'].min()}")
        print(f"  Newest entry: {cache_df['cached_at'].max()}")


def main():
    """Example usage"""
    
    print("=" * 70)
    print("ICESat-2 VEGETATION HEIGHT WITH CACHING")
    print("=" * 70)
    print()
    
    # Example 1: Single location
    print("Example 1: Single Location Query")
    print("-" * 70)
    
    lat = 41.868074
    lon = -124.152736
    
    result = get_icesat2_with_cache(lat, lon)
    
    if result:
        print(f"\nResult:")
        print(f"  Vegetation height: {result['vegetation_height_m']:.2f} m")
        print(f"  Ground elevation: {result['ground_elevation_m']:.2f} m")
        print(f"  Canopy elevation: {result['canopy_elevation_m']:.2f} m")
        print(f"  Measurement date: {result['date']}")
        print(f"  Distance from query: {result['distance_km']:.3f} km")
        print(f"  Data source: {result['data_source']}")
    
    print()
    
    # Example 2: Multiple locations
    print("\nExample 2: Batch Query (Multiple Locations)")
    print("-" * 70)
    
    locations = [
        (41.868, -124.153),  # Fort Dick, CA
        (41.870, -124.150),  # Nearby point 1
        (41.865, -124.155),  # Nearby point 2
    ]
    
    df = batch_get_vegetation_heights(locations)
    
    if not df.empty:
        print("\n" + "=" * 70)
        print("RESULTS SUMMARY")
        print("=" * 70)
        print(df[['latitude', 'longitude', 'vegetation_height_m', 'distance_km', 'data_source']])
        
        # Save results
        output_file = "vegetation_results.csv"
        df.to_csv(output_file, index=False)
        print(f"\n✓ Saved results to: {output_file}")
    
    # Example 3: Cache statistics
    print("\n")
    cache_statistics()
    
    print("\n" + "=" * 70)
    print("USAGE NOTES")
    print("=" * 70)
    print("""
Performance Improvement:
  ✓ First query: Fetches from ICESat-2 API (~5-30 seconds)
  ✓ Subsequent queries: Instant (from cache)
  ✓ Reduces API load and speeds up analysis

Cache Management:
  - Cache file: icesat2_cache.csv
  - Tolerance: 500 meters (configurable)
  - To clear cache: clear_cache()
  - To view stats: cache_statistics()

Best Practices:
  1. Let it build cache naturally as you query
  2. Check cache stats periodically
  3. Clear cache if data becomes outdated
  4. Use force refresh (use_cache=False) for new data
    """)


if __name__ == '__main__':
    main()