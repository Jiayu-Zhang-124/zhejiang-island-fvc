from fastapi import FastAPI, File, UploadFile, HTTPException, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
import uvicorn
import rasterio
from rasterio.merge import merge
import numpy as np
from scipy.stats import theilslopes, kendalltau, norm
import os
import tempfile
import json
from typing import Optional, List, cast
import ee
import gc
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
import matplotlib.gridspec as gridspec
import io
import base64
import asyncio
from concurrent.futures import ThreadPoolExecutor
import time

# --- Configure matplotlib for publication quality ---
plt.rcParams.update({
    'font.family': ['serif', 'sans-serif'],
    'font.serif': ['Times New Roman', 'SimSun', 'DejaVu Serif', 'serif'],
    'font.sans-serif': ['Times New Roman', 'SimHei', 'Microsoft YaHei', 'sans-serif'],
    'axes.unicode_minus': False, # Resolve minus sign issue with Chinese fonts
    'font.size': 10,
    'axes.labelsize': 11,
    'axes.titlesize': 12,
    'xtick.labelsize': 9,
    'ytick.labelsize': 9,
    'legend.fontsize': 9,
    'figure.dpi': 300,
    'savefig.dpi': 300,
    'axes.linewidth': 0.8,
    'xtick.major.width': 0.6,
    'ytick.major.width': 0.6,
    'lines.linewidth': 1.5,
    'lines.markersize': 6,
})

app = FastAPI(title="FVC Temporal Mosaic Analysis API")

# Thread pool for CPU-bound tasks (rasterio + matplotlib)
executor = ThreadPoolExecutor(max_workers=4)

# Global state for spatial trend progress tracking
spatial_task_status = {"percent": 0, "status": "idle"}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_session_root():
    """Internal helper for session-based temporary file storage."""
    root = os.path.join(os.path.dirname(__file__), "workspace_records", "session")
    os.makedirs(root, exist_ok=True)
    return root


class MosaicResult(BaseModel):
    value: float
    year: str
    bbox: List[float]
    previewBase64: str

class ClimateResult(BaseModel):
    timeline: List[str]
    tempSeries: List[float]
    precipSeries: List[float]
    geeStatus: str

def init_gee(json_data: dict | str):
    try:
        if isinstance(json_data, str):
            creds_data = json.loads(json_data)
        else:
            creds_data = json_data
            
        credentials = ee.ServiceAccountCredentials(creds_data['client_email'], key_data=json.dumps(creds_data))
        ee.Initialize(credentials)
        return True
    except Exception as e:
        print(f"GEE Init Error: {e}")
        return False

def downsample_array(arr, max_pixels=800):
    """Downsample a 2D array using block mean pooling to preserve sparse data,
    speeding up matplotlib rendering without losing small features."""
    h, w = arr.shape
    if max(h, w) <= max_pixels:
        return arr
        
    factor = int(np.ceil(max(h, w) / max_pixels))
    if factor < 2:
        factor = 2
        
    h_new = (h // factor) * factor
    w_new = (w // factor) * factor
    arr_trunc = arr[:h_new, :w_new]
    
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        # Block average, ignoring NaNs, to keep small island features visible
        pooled = np.nanmean(arr_trunc.reshape(h_new//factor, factor, w_new//factor, factor), axis=(1, 3))
    
    return pooled

def calculate_mosaic_metric(tif_paths: List[str], metric_type: str = "FVC", save_path: Optional[str] = None):
    """CPU-bound: merge tiles, compute mean Metric, generate preview, and optionally save the mosaic."""
    t0 = time.time()
    src_files_to_mosaic = []
    try:
        for fp in tif_paths:
            src = rasterio.open(fp)
            src_files_to_mosaic.append(src)
            
        # Merge all tiles with method='first' for speed
        mosaic, out_trans = merge(src_files_to_mosaic, method='first')
        t1 = time.time()
        print(f"  [perf] rasterio.merge: {t1 - t0:.2f}s")
        
        # Calculate bounds
        height, width = mosaic.shape[1], mosaic.shape[2]
        left, top = out_trans * (0, 0)
        right, bottom = out_trans * (width, height)
        bbox = [min(left, right), min(top, bottom), max(left, right), max(top, bottom)]
        
        band1 = mosaic[0]
        nodata = src_files_to_mosaic[0].nodata
        
        # Save the mosaic if requested (useful for pixel-based trend analysis)
        if save_path:
            out_meta = src_files_to_mosaic[0].meta.copy()
            out_meta.update({
                "driver": "GTiff",
                "height": height,
                "width": width,
                "transform": out_trans,
                "count": 1,
                "dtype": band1.dtype,
                "compress": "lzw" if metric_type == "NDVI" else "deflate"
            })
            if nodata is not None:
                out_meta["nodata"] = nodata
                
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            with rasterio.open(save_path, "w", **out_meta) as dest:
                dest.write(band1, 1)
        
        if metric_type == "NDVI":
            valid_mask = (band1 >= -1.0) & (band1 <= 1.0)
            vmin, vmax = -1.0, 1.0
        else: # FVC
            valid_mask = (band1 >= 0.0) & (band1 <= 1.0)
            vmin, vmax = 0.0, 1.0
            
        if nodata is not None:
            valid_mask &= (band1 != nodata)
            
        valid_pixels = band1[valid_mask]
        mean_val = float(np.mean(valid_pixels)) if len(valid_pixels) > 0 else 0.0
        t2 = time.time()
        print(f"  [perf] {metric_type} compute: {t2 - t1:.2f}s")

        # Generate Visual Map Preview as SVG (vector - scales perfectly)
        vis_array = np.full(band1.shape, np.nan)
        vis_array[valid_mask] = band1[valid_mask]
        
        # Clear large arrays from memory as soon as possible
        del band1
        del valid_mask
        del valid_pixels
        del mosaic
        
        # Downsample for preview rendering (huge speedup for large images)
        vis_small = downsample_array(vis_array, max_pixels=800)
        del vis_array
        gc.collect()

        fig, ax = plt.subplots(figsize=(5, 5))
        ax.axis('off')
        fig.subplots_adjust(left=0, right=0.88, top=1, bottom=0)
        
        im = ax.imshow(vis_small, cmap='RdYlGn', vmin=vmin, vmax=vmax, interpolation='nearest')
        cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        cbar.set_label(f'{metric_type} Value', color='white', fontsize=8)
        cbar.ax.yaxis.set_tick_params(color='white', labelcolor='white', labelsize=7)
        cbar.outline.set_edgecolor('white')

        buf = io.BytesIO()
        plt.savefig(buf, format='svg', bbox_inches='tight', transparent=True)
        buf.seek(0)
        preview_b64 = base64.b64encode(buf.read()).decode('utf-8')
        plt.close('all')
        
        t3 = time.time()
        print(f"  [perf] matplotlib render: {t3 - t2:.2f}s")
        print(f"  [perf] TOTAL: {t3 - t0:.2f}s (original pixels: {height}x{width}, preview: {vis_small.shape[0]}x{vis_small.shape[1]})")

        return round(mean_val, 4), bbox, preview_b64
    finally:
        for src in src_files_to_mosaic:
            src.close()
        src_files_to_mosaic.clear()
        gc.collect()

@app.post("/api/analyze_mosaic", response_model=MosaicResult)
async def analyze_mosaic(
    files: List[UploadFile] = File(...),
    year: str = Form(...),
    metric_type: str = Form("FVC")
):
    """Takes multiple TIF files representing one year's tiles, mosaics them, and computes Metric."""
    temp_files = []
    
    try:
        # Read all files concurrently using asyncio.gather
        async def save_one_file(f):
            if not f.filename.lower().endswith(('.tif', '.tiff')):
                return None
            content = await f.read()
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".tif")
            tmp.write(content)
            tmp.close()
            return tmp.name
        
        results = await asyncio.gather(*[save_one_file(f) for f in files])
        temp_files = [r for r in results if r is not None]
        
        if not temp_files:
            raise HTTPException(status_code=400, detail="No valid TIF files provided.")
        
        current_path = get_session_root()
        os.makedirs(current_path, exist_ok=True)
        save_path = os.path.join(current_path, f"{year}.tif")
        
        # Run the CPU-heavy mosaic+render in a thread pool to avoid blocking the event loop
        loop = asyncio.get_event_loop()
        mean_val, bbox, preview_b64 = await loop.run_in_executor(
            executor, calculate_mosaic_metric, temp_files, metric_type, save_path
        )
        
        # Save tracking info for latest session
        with open(os.path.join(current_path, "metric_info.json"), 'w') as f:
            json.dump({"metric_type": metric_type}, f)

        return MosaicResult(
            value=mean_val,
            year=year,
            bbox=bbox,
            previewBase64=preview_b64
        )
    finally:
        for fp in temp_files:
            if os.path.exists(fp):
                os.unlink(fp)


# --- Constants ---
def get_default_key_path():
    # 1. First priority: Environment variable (good for Render/Docker)
    env_json = os.environ.get("GEE_JSON")
    if env_json:
        return "ENV"

    base_dir = os.path.dirname(__file__)
    # Support both gee_key.json and the common Windows "gee_key.json.json" mistake
    paths = [
        os.path.join(base_dir, "gee_key.json"),
        os.path.join(base_dir, "gee_key.json.json")
    ]
    for p in paths:
        if os.path.exists(p):
            return p
    return None

@app.get("/api/check_gee")
async def check_gee_status():
    """Check if a default GEE key is available locally or in ENV."""
    status = get_default_key_path()
    return {"available": status is not None}

@app.post("/api/climate", response_model=ClimateResult)
async def get_climate(
    gee_key: Optional[UploadFile] = File(None),
    years: str = Form(...),
    bbox: str = Form(...),
    month_ranges: Optional[str] = Form(None) # JSON string: {"2024": "04-10"}
):
    """Extracts climate data for specific years from GEE or uses fallback."""
    year_list = sorted([y.strip() for y in years.split(",") if y.strip()])
    bbox_list = [float(x.strip()) for x in bbox.split(",")][:4]
    
    parsed_ranges: dict[str, str] = {}
    if month_ranges:
        try:
            parsed_ranges = cast(dict[str, str], json.loads(month_ranges))
        except:
            print("Error parsing month_ranges JSON")

    if len(year_list) == 0:
        year_list = ["2024"]
    
    timeline = []
    temp_series = []
    precip_series = []
    gee_status = "Not used"
    
    # Path to the JSON key to use
    key_path = None
    is_temp = False

    # 1. Check if user uploaded a new key
    if gee_key:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp_json:
            tmp_json.write(await gee_key.read())
            key_path = tmp_json.name
            is_temp = True
    # 2. Check environment variable
    elif os.environ.get("GEE_JSON"):
        env_json = os.environ.get("GEE_JSON")
        if init_gee(env_json):
            gee_status = "Connected (Cloud ENV)"
        else:
            gee_status = "Error: Invalid GEE_JSON ENV"
    # 3. Check default local path
    else:
        key_path = get_default_key_path()
        if key_path and key_path != "ENV": # Local file case
            if init_gee(key_path):
                gee_status = "Connected (Server Auto-detect)"
            else:
                gee_status = "Error: Local Key Init Failed"

    # If we have a physical key file (upload or local), initialize it
    if key_path and key_path != "ENV" and "Connected" not in gee_status:
        # For uploaded/local files, we need to read them
        try:
            with open(key_path, 'r') as f:
                json_data = json.load(f)
            if init_gee(json_data):
                gee_status = "Connected (Uploaded/Local File)"
            else:
                gee_status = "Error: File Init Failed"
        except Exception as e:
            gee_status = f"Error: Key Read Failed - {str(e)}"
    
    # GEE Approach
    if "Connected" in gee_status:
        try:
            region = ee.Geometry.Rectangle(bbox_list)
            era5 = ee.ImageCollection("ECMWF/ERA5_LAND/MONTHLY_BY_HOUR")
            
            for y in year_list:
                # Use year-specific month range if available, default to full year
                m_range = parsed_ranges.get(y, "01-12")
                try:
                    start_m, end_m = map(int, m_range.split("-"))
                except:
                    start_m, end_m = 1, 12
                
                start_date = f"{int(y)}-01-01"
                end_date = f"{int(y)+1}-01-01"
                
                # Filter by date first
                annual = era5.filterBounds(region).filterDate(start_date, end_date)
                
                # Filter by specific months for growing season
                if start_m != 1 or end_m != 12:
                    annual = annual.filter(ee.Filter.calendarRange(start_m, end_m, 'month'))
                
                mean_temp_img = annual.select('temperature_2m').mean().subtract(273.15)
                total_precip_img = annual.select('total_precipitation').sum().multiply(1000)
                
                stats = ee.Image.cat([mean_temp_img, total_precip_img]).reduceRegion(
                    reducer=ee.Reducer.mean(),
                    geometry=region,
                    scale=11132
                ).getInfo()
                
                if stats and 'temperature_2m' in stats:
                    timeline.append(y)
                    temp_series.append(round(stats['temperature_2m'], 1))
                    if 'total_precipitation' in stats:
                        precip_series.append(round(stats['total_precipitation'], 1))
                    else:
                        precip_series.append(0.0)
                    gee_status = "Success"
                else:
                    print(f"No data for year {y}")
            
            if not timeline:
                gee_status = "No valid data found for these years"
        except Exception as e:
            gee_status = f"GEE Query Error: {str(e)[:50]}"
    else:
        gee_status = "GEE Authorization Failed or Not Provided"
    
    # Cleanup temp file if exists
    if is_temp and key_path and os.path.exists(key_path):
        try:
            os.unlink(key_path)
        except:
            pass

    # Fallback to random if GEE fail or missing
    if len(timeline) == 0:
        timeline = year_list
        np.random.seed(42)
        gee_status = "Mock Data (Fallback)"
        for y in year_list:
            temp_series.append(round(16.0 + np.random.normal(0, 0.5), 1))
            precip_series.append(round(1400 + np.random.normal(0, 100), 1))
            
    return ClimateResult(
        timeline=timeline,
        tempSeries=temp_series,
        precipSeries=precip_series,
        geeStatus=gee_status
    )

# ============ Trend Analysis: Theil-Sen + Mann-Kendall ============

def mann_kendall_test(data):
    """Perform Mann-Kendall trend test."""
    n = len(data)
    s = 0
    for k in range(n - 1):
        for j in range(k + 1, n):
            s += int(np.sign(data[j] - data[k]))
    
    # Variance with tie correction
    unique, counts = np.unique(data, return_counts=True)
    tp = counts[counts > 1]
    var_s = (n * (n - 1) * (2 * n + 5)) / 18
    if len(tp) > 0:
        var_s -= np.sum(tp * (tp - 1) * (2 * tp + 5)) / 18
    
    # Standardized Z
    if var_s <= 0:
        z = 0.0
    elif s > 0:
        z = (s - 1) / np.sqrt(var_s)
    elif s < 0:
        z = (s + 1) / np.sqrt(var_s)
    else:
        z = 0.0
    
    p_value = 2 * (1 - norm.cdf(abs(z)))
    tau, _ = kendalltau(np.arange(n), data)
    
    return int(s), float(z), float(p_value), float(tau)

def analyze_trend(series, label=""):
    """Combined Theil-Sen slope + Mann-Kendall test."""
    data = np.array(series, dtype=float)
    x = np.arange(len(data))
    
    slope, intercept, _, _ = theilslopes(data, x)
    trend_line = (slope * x + intercept).tolist()
    
    s, z, p_value, tau = mann_kendall_test(data)
    
    if p_value < 0.01:
        sig = "★★★ 极显著上升" if slope > 0 else "★★★ 极显著下降"
        sig_en = "★★★ Highly Sig. Increase" if slope > 0 else "★★★ Highly Sig. Decrease"
        trend = "significant_increase" if slope > 0 else "significant_decrease"
    elif p_value < 0.05:
        sig = "★★ 显著上升" if slope > 0 else "★★ 显著下降"
        sig_en = "★★ Significant Increase" if slope > 0 else "★★ Significant Decrease"
        trend = "significant_increase" if slope > 0 else "significant_decrease"
    elif p_value < 0.1:
        sig = "★ 弱显著上升" if slope > 0 else "★ 弱显著下降"
        sig_en = "★ Weakly Sig. Increase" if slope > 0 else "★ Weakly Sig. Decrease"
        trend = "weak_increase" if slope > 0 else "weak_decrease"
    else:
        sig = "无显著趋势"
        sig_en = "No Significant Trend"
        trend = "no_trend"
    
    return {
        "theilSenSlope": round(float(slope), 6),
        "theilSenIntercept": round(float(intercept), 6),
        "mkStatistic": int(s),
        "mkZScore": round(float(z), 4),
        "mkPValue": round(float(p_value), 6),
        "mkTau": round(float(tau), 4),
        "trend": trend,
        "significance": sig,
        "significance_en": sig_en,
        "trendLine": [round(v, 6) for v in trend_line]
    }

def vectorized_mann_kendall(data_3d, valid_mask_2d, chunk_size=2000):
    """
    Vectorized computation of Mann-Kendall Z-score and P-value for a 3D array (time, height, width)
    computed in chunks to avoid OOM errors on large scenes.
    """
    n, h, w = data_3d.shape
    z_score = np.zeros((h, w), dtype=np.float32)
    p_value = np.ones((h, w), dtype=np.float32)
    
    var_s = (n * (n - 1) * (2 * n + 5)) / 18.0
    
    for y in range(0, h, chunk_size):
        for x in range(0, w, chunk_size):
            # Define chunk slices
            y_end = min(y + chunk_size, h)
            x_end = min(x + chunk_size, w)
            y_slice, x_slice = slice(y, y_end), slice(x, x_end)
            
            mask_chunk = valid_mask_2d[y_slice, x_slice]
            
            # Skip chunk if entirely invalid
            if not np.any(mask_chunk):
                continue
                
            data_chunk = data_3d[:, y_slice, x_slice]
            s_stat_chunk = np.zeros((y_end - y, x_end - x), dtype=np.int32)
            
            for k in range(n - 1):
                for j in range(k + 1, n):
                    diff = data_chunk[j] - data_chunk[k]
                    s_stat_chunk[mask_chunk] += np.sign(diff[mask_chunk]).astype(np.int32)
            
            # Compute Z and P just for this chunk
            z_chunk = np.zeros(s_stat_chunk.shape, dtype=np.float32)
            
            mask_gt = mask_chunk & (s_stat_chunk > 0)
            z_chunk[mask_gt] = (s_stat_chunk[mask_gt] - 1) / np.sqrt(var_s)
            
            mask_lt = mask_chunk & (s_stat_chunk < 0)
            z_chunk[mask_lt] = (s_stat_chunk[mask_lt] + 1) / np.sqrt(var_s)
            
            p_chunk = np.ones(s_stat_chunk.shape, dtype=np.float32)
            p_chunk[mask_chunk] = 2 * (1 - norm.cdf(np.abs(z_chunk[mask_chunk])))
            
            # Write back to full arrays
            z_score[y_slice, x_slice] = z_chunk
            p_value[y_slice, x_slice] = p_chunk

    return z_score, p_value

def vectorized_theil_sen(data_3d, valid_mask_2d, chunk_size=2000):
    """
    Vectorized computation of Theil-Sen Median Slope, processed in chunks
    to handle very large 3D configurations without running out of RAM.
    """
    n, h, w = data_3d.shape
    median_slope = np.zeros((h, w), dtype=np.float32)
    
    for y in range(0, h, chunk_size):
        for x in range(0, w, chunk_size):
            y_end = min(y + chunk_size, h)
            x_end = min(x + chunk_size, w)
            y_slice, x_slice = slice(y, y_end), slice(x, x_end)
            
            mask_chunk = valid_mask_2d[y_slice, x_slice]
            
            if not np.any(mask_chunk):
                continue
                
            data_chunk = data_3d[:, y_slice, x_slice]
            slopes_chunk = []
            
            for k in range(n - 1):
                for j in range(k + 1, n):
                    time_diff = j - k
                    slope = np.full(data_chunk[0].shape, np.nan, dtype=np.float32)
                    slope[mask_chunk] = (data_chunk[j][mask_chunk] - data_chunk[k][mask_chunk]) / time_diff
                    slopes_chunk.append(slope)
            
            slopes_stacked = np.stack(slopes_chunk, axis=0)
            valid_coords = np.where(mask_chunk)
            
            valid_slopes_only = slopes_stacked[:, valid_coords[0], valid_coords[1]]
            median_vals = np.median(valid_slopes_only, axis=0)
            
            median_chunk = np.zeros(mask_chunk.shape, dtype=np.float32)
            median_chunk[valid_coords[0], valid_coords[1]] = median_vals
            
            median_slope[y_slice, x_slice] = median_chunk
            
    return median_slope


class TrendRequest(BaseModel):
    timeline: List[str]
    metricSeries: List[float]
    metricType: str
    tempSeries: List[float]
    precipSeries: List[float]

@app.post("/api/trend_analysis")
async def trend_analysis(request: TrendRequest):
    """Perform Theil-Sen + Mann-Kendall trend analysis on all series."""
    results = {}
    if len(request.metricSeries) >= 3:
        results["metric"] = analyze_trend(request.metricSeries, request.metricType)
    if len(request.tempSeries) >= 3:
        results["temperature"] = analyze_trend(request.tempSeries, "Temperature")
    if len(request.precipSeries) >= 3:
        results["precipitation"] = analyze_trend(request.precipSeries, "Precipitation")
    return results

class ExportRequest(BaseModel):
    timeline: List[str]
    metricSeries: List[float]
    metricType: str
    tempSeries: List[float]
    precipSeries: List[float]
    previews: List[dict]  # [{year: str, base64: str}]
    trendData: Optional[dict] = None  # Trend analysis results

@app.get("/api/spatial_progress")
async def get_spatial_progress():
    return spatial_task_status

class SpatialTrendRequest(BaseModel):
    timeline: List[str]
    metricType: str
    tempSeries: List[float]
    precipSeries: List[float]

@app.post("/api/spatial_trend")
def calculate_spatial_trend(request: SpatialTrendRequest):
    """
    Computes purely pixel-based Sen+MK trends using saved mosaiced TIFs.
    Returns: B64 image of the spatial map, and a summary table with area % and correlations.
    Reads data efficiently in spatial chunks to prevent Out-Of-Memory (OOM) errors.
    """
    try:
        t0 = time.time()
        base_dir = os.path.dirname(__file__)
        workspace_dir = os.path.join(base_dir, "workspace_mosaics")
        
        years = request.timeline
        if len(years) < 3:
            raise HTTPException(status_code=400, detail="Spatial trend analysis requires at least 3 years of data.")
            
        srcs = []
        for year in years:
            fp = os.path.join(workspace_dir, f"{year}.tif")
            if not os.path.exists(fp):
                for s in srcs:
                    s.close()
                raise HTTPException(status_code=400, detail=f"Mosaic for {year} not found in workspace.")
            srcs.append(rasterio.open(fp))
            
        n_years = len(years)
        h = srcs[0].height
        w = srcs[0].width
        nodata = srcs[0].nodata
        chunk_size = 2000
        
        print(f"[spatial] Setup {n_years} years. Image shape ({h}, {w}). Processing in chunks...")
        
        spatial_task_status["status"] = "processing"
        
        # Calculate total chunks for progress tracking
        num_x_chunks = (w + chunk_size - 1) // chunk_size
        num_y_chunks = (h + chunk_size - 1) // chunk_size
        total_chunks = num_x_chunks * num_y_chunks
        processed_chunks = 0

        # Final output arrays covering the entire image map
        trend_class = np.zeros((h, w), dtype=np.uint8)
        corr_temp = np.zeros((h, w), dtype=np.float32)
        corr_precip = np.zeros((h, w), dtype=np.float32)
        valid_mask_2d_full = np.zeros((h, w), dtype=bool)
        
        # Climate series references format
        temp_arr = np.array(request.tempSeries, dtype=np.float32).reshape(n_years, 1, 1)
        precip_arr = np.array(request.precipSeries, dtype=np.float32).reshape(n_years, 1, 1)

        def pearson_corr_chunk(x_3d, y_3d, mask, ch_h, ch_w):
            """Fast pixel-wise Pearson correlation for a chunk."""
            x_mean = np.mean(x_3d, axis=0, keepdims=True)
            y_mean = np.mean(y_3d, axis=0, keepdims=True)
            xm = x_3d - x_mean
            ym = y_3d - y_mean
            num = np.sum(xm * ym, axis=0)
            den = np.sqrt(np.sum(xm**2, axis=0) * np.sum(ym**2, axis=0))
            corr = np.zeros((ch_h, ch_w), dtype=np.float32)
            valid_den = mask & (den != 0)
            corr[valid_den] = num[valid_den] / den[valid_den]
            return corr

        from rasterio.windows import Window
        
        # Process the entire images block by block to avoid memory blowup
        for y in range(0, h, chunk_size):
            y_len = min(chunk_size, h - y)
            for x in range(0, w, chunk_size):
                x_len = min(chunk_size, w - x)
                window = Window(x, y, x_len, y_len)
                
                # 1. Load data for just this local chunk
                data_chunk = np.zeros((n_years, y_len, x_len), dtype=np.float32)
                for i, src in enumerate(srcs):
                    data_chunk[i] = src.read(1, window=window)
                    
                # 2. Derive valid mask locally
                if request.metricType == "NDVI":
                    valid_mask_3d = (data_chunk >= -1.0) & (data_chunk <= 1.0)
                else:
                    valid_mask_3d = (data_chunk >= 0.0) & (data_chunk <= 1.0)
                    
                if nodata is not None:
                    valid_mask_3d &= (data_chunk != nodata)
                    
                valid_mask_2d_chunk = np.all(valid_mask_3d, axis=0)
                valid_mask_2d_full[y:y+y_len, x:x+x_len] = valid_mask_2d_chunk
                
                if np.any(valid_mask_2d_chunk):
                    # 3. Vectorized algorithms on the local chunk
                    z_score, p_value = vectorized_mann_kendall(data_chunk, valid_mask_2d_chunk)
                    slope_2d = vectorized_theil_sen(data_chunk, valid_mask_2d_chunk)
                    
                    # 4. Classify trends locally
                    mask_inc = valid_mask_2d_chunk & (slope_2d > 0)
                    mask_dec = valid_mask_2d_chunk & (slope_2d < 0)
                    highly_sig = p_value < 0.01
                    sig = (p_value >= 0.01) & (p_value < 0.05)
                    no_trend = p_value >= 0.05
                    
                    t_class = np.zeros((y_len, x_len), dtype=np.uint8)
                    t_class[mask_inc & highly_sig] = 1 # Highly Significant Increase
                    t_class[mask_inc & sig] = 2        # Significant Increase
                    t_class[valid_mask_2d_chunk & no_trend] = 3 # No Significant Trend
                    t_class[mask_dec & sig] = 4        # Significant Decrease
                    t_class[mask_dec & highly_sig] = 5 # Highly Significant Decrease
                    t_class[valid_mask_2d_chunk & (slope_2d == 0)] = 3
                    
                    trend_class[y:y+y_len, x:x+x_len] = t_class
                    
                    # 5. Climate Correlations locally
                    corr_t = pearson_corr_chunk(data_chunk, temp_arr, valid_mask_2d_chunk, y_len, x_len)
                    corr_temp[y:y+y_len, x:x+x_len] = corr_t
                    
                    corr_p = pearson_corr_chunk(data_chunk, precip_arr, valid_mask_2d_chunk, y_len, x_len)
                    corr_precip[y:y+y_len, x:x+x_len] = corr_p

                processed_chunks += 1
                spatial_task_status["percent"] = int((processed_chunks / total_chunks) * 100)

        # Close all opened TIF srcs
        for src in srcs:
            src.close()
            
        spatial_task_status["percent"] = 100
        spatial_task_status["status"] = "idle"

        total_valid_pixels = np.sum(valid_mask_2d_full)
        if total_valid_pixels == 0:
            raise HTTPException(status_code=400, detail="No valid pixels found across all years.")

        # 6. Generate Statistics Table mapping to codes (0-5)
        print("[spatial] Computing overall statistics...")
        class_names = [
            "Invalid",
            "Highly Significant Increase",
            "Significant Increase",
            "No Significant Trend",
            "Significant Decrease",
            "Highly Significant Decrease"
        ]
        
        stats_table = []
        for i in range(1, 6):
            class_mask = (trend_class == i)
            count = np.sum(class_mask)
            if count > 0:
                area_pct = (count / total_valid_pixels) * 100
                avg_tmp = np.mean(corr_temp[class_mask])
                avg_prc = np.mean(corr_precip[class_mask])
            else:
                area_pct = 0.0
                avg_tmp = 0.0
                avg_prc = 0.0
                
            stats_table.append({
                "trendClass": class_names[i],
                "areaPercentage": round(float(area_pct), 2),
                "pixelCount": int(count),
                "avgTempCorr": round(float(avg_tmp), 4),
                "avgPrecipCorr": round(float(avg_prc), 4)
            })

        # 7. Generate Map Visualization Image 
        print("[spatial] Generating Map Visualization (Scientific Quality)...")
        from matplotlib.colors import ListedColormap, BoundaryNorm
        import matplotlib.patches as mpatches
        
        # Professional color palette (Green-Red with neutral middle)
        colors = ['#1a9850', '#91cf60', '#f7f7f7', '#fc8d59', '#d73027']
        cmap = ListedColormap(colors)
        bounds = [0.5, 1.5, 2.5, 3.5, 4.5, 5.5]
        norm = BoundaryNorm(bounds, cmap.N)
        
        vis_class = np.full((h, w), np.nan)
        vis_class[valid_mask_2d_full] = trend_class[valid_mask_2d_full]
        
        # Subsample for fast processing, higher limit for detail
        vis_small = downsample_array(vis_class, max_pixels=2000)
        
        # Get geographic bounds for axes
        l, b, r, t = srcs[0].bounds
        
        fig, ax = plt.subplots(figsize=(8, 7), dpi=300)
        fig.patch.set_facecolor('white')
        
        # Use extent to map pixel indices to real geographic coordinates
        im = ax.imshow(vis_small, cmap=cmap, norm=norm, interpolation='nearest', extent=[l, r, b, t])
        
        # Style Axes
        ax.set_xlabel(r'Longitude ($^\circ$E)', fontsize=10, fontname='Times New Roman')
        ax.set_ylabel(r'Latitude ($^\circ$N)', fontsize=10, fontname='Times New Roman')
        ax.tick_params(labelsize=8)
        for label in ax.get_xticklabels() + ax.get_yticklabels():
            label.set_fontname('Times New Roman')
        
        # Add North Arrow
        ax.annotate('N', xy=(0.02, 0.98), xytext=(0.02, 0.92),
                    xycoords='axes fraction', arrowprops=dict(facecolor='black', width=3, headwidth=8),
                    horizontalalignment='center', verticalalignment='top', fontsize=12, fontweight='bold')

        # Add Scale Bar (Approximate)
        # Calculate width of 1/5th of the view in meters (assuming EPSG:4320 or similar lat/lon)
        # For simplicity, if it's lat/lon, 0.01 degree is ~1.1km. 
        # We can just draw a 0.05 degree line if the range is small.
        view_width = r - l
        scale_val = 0.01 if view_width < 0.1 else (0.1 if view_width < 1.0 else 1.0)
        scale_label = fr"{scale_val}$^\circ$"
        ax.plot([r - 0.05 - scale_val, r - 0.05], [b + 0.05, b + 0.05], color='black', linewidth=2)
        ax.text(r - 0.05 - scale_val/2, b + 0.06, scale_label, horizontalalignment='center', fontsize=8, fontname='Times New Roman')

        # Refined Legend
        patches = [mpatches.Patch(color=colors[i], label=class_names[i+1]) for i in range(5)]
        leg = ax.legend(handles=patches, bbox_to_anchor=(1.02, 1), loc='upper left', 
                       borderaxespad=0., fontsize=8, frameon=True, edgecolor='black')
        for text in leg.get_texts():
            text.set_fontname('Times New Roman')
            
        plt.title(f'Spatial Pattern of {request.metricType} Trend ({years[0]}-{years[-1]})', 
                  fontsize=12, fontweight='bold', pad=15, fontname='Times New Roman')
        
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=300, bbox_inches='tight', facecolor='white')
        buf.seek(0)
        map_b64 = base64.b64encode(buf.read()).decode('utf-8')
        plt.close(fig)
        
        t1 = time.time()
        print(f"[spatial] Total Spatial Analysis Time: {t1-t0:.2f}s")
        
        return {
            "status": "success",
            "mapBase64": map_b64,
            "statistics": stats_table
        }
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

def add_stats_box(ax, td_part, prefix=""):
    if td_part:
        slope = td_part['theilSenSlope']
        z = td_part['mkZScore']
        p = td_part['mkPValue']
        
        # English significance labels
        if p < 0.01:
            sig_en = "★★★ Highly Sig. Increase" if slope > 0 else "★★★ Highly Sig. Decrease"
        elif p < 0.05:
            sig_en = "★★ Significant Increase" if slope > 0 else "★★ Significant Decrease"
        elif p < 0.1:
            sig_en = "★ Weakly Sig. Increase" if slope > 0 else "★ Weakly Sig. Decrease"
        else:
            sig_en = "No Significant Trend"
            
        text = f"{prefix}Slope: {slope:+.4f}/yr\nZ: {z:.3f}, p: {p:.4f}\n{sig_en}"
        ax.text(0.02, 0.95, text, transform=ax.transAxes, fontsize=10,
                verticalalignment='top',
                bbox=dict(boxstyle='round,pad=0.5', facecolor='white', alpha=0.8, edgecolor='gray'))

def generate_trend_figure(data: ExportRequest) -> bytes:
    """Generate a publication-quality trend analysis figure."""
    years = [str(y) for y in data.timeline]
    x = range(len(years))
    
    fig = plt.figure(figsize=(10, 15))
    gs = gridspec.GridSpec(3, 1, hspace=0.4)

    # --- 1. Metric Plot ---
    ax_metric = fig.add_subplot(gs[0])
    color_metric = '#2E7D32'
    ax_metric.scatter(x, data.metricSeries, color=color_metric, s=50, label='Annual Mean', zorder=5)
    
    if data.trendData and 'metric' in data.trendData and 'trendLine' in data.trendData['metric']:
        ax_metric.plot(x, data.trendData['metric']['trendLine'], '--', color='#1B5E20', linewidth=2, label='Theil-Sen Trend', zorder=4)
        add_stats_box(ax_metric, data.trendData['metric'], f"{data.metricType} ")
        
    ax_metric.set_title(f'(a) {data.metricType} Trend Analysis', fontweight='bold')
    ax_metric.set_ylabel(f'Mean {data.metricType}', fontweight='bold')
    ax_metric.set_xticks(x)
    ax_metric.set_xticklabels(years, rotation=45 if len(years) > 10 else 0)
    ax_metric.grid(True, linestyle='--', alpha=0.5)
    ax_metric.legend(loc='upper right')
    # Prevent overlap
    y_min, y_max = ax_metric.get_ylim()
    ax_metric.set_ylim(y_min, y_max + (y_max - y_min) * 0.4)

    # --- 2. Temperature Plot ---
    ax_temp = fig.add_subplot(gs[1])
    color_temp = '#D32F2F'
    ax_temp.scatter(x, data.tempSeries, color=color_temp, marker='s', s=40, label='Annual Mean', zorder=5)
    
    if data.trendData and 'temperature' in data.trendData and 'trendLine' in data.trendData['temperature']:
        ax_temp.plot(x, data.trendData['temperature']['trendLine'], '--', color='#B71C1C', linewidth=2, label='Theil-Sen Trend', zorder=4)
        add_stats_box(ax_temp, data.trendData['temperature'], "Temp ")
        
    ax_temp.set_title('(b) Temperature Trend Analysis', fontweight='bold')
    ax_temp.set_ylabel('Temperature (°C)', fontweight='bold')
    ax_temp.set_xticks(x)
    ax_temp.set_xticklabels(years, rotation=45 if len(years) > 10 else 0)
    ax_temp.grid(True, linestyle='--', alpha=0.5)
    ax_temp.legend(loc='upper right')
    # Prevent overlap
    y_min, y_max = ax_temp.get_ylim()
    ax_temp.set_ylim(y_min, y_max + (y_max - y_min) * 0.4)

    # --- 3. Precipitation Plot ---
    ax_precip = fig.add_subplot(gs[2])
    color_precip = '#1565C0'
    ax_precip.scatter(x, data.precipSeries, color=color_precip, marker='D', s=40, label='Annual Total', zorder=5)
    
    if data.trendData and 'precipitation' in data.trendData and 'trendLine' in data.trendData['precipitation']:
        ax_precip.plot(x, data.trendData['precipitation']['trendLine'], '--', color='#0D47A1', linewidth=2, label='Theil-Sen Trend', zorder=4)
        add_stats_box(ax_precip, data.trendData['precipitation'], "Precip ")
        
    ax_precip.set_title('(c) Precipitation Trend Analysis', fontweight='bold')
    ax_precip.set_xlabel('Year', fontweight='bold')
    ax_precip.set_ylabel('Precipitation (mm)', fontweight='bold')
    ax_precip.set_xticks(x)
    ax_precip.set_xticklabels(years, rotation=45 if len(years) > 10 else 0)
    ax_precip.grid(True, linestyle='--', alpha=0.5)
    ax_precip.legend(loc='upper right')
    # Prevent overlap
    y_min, y_max = ax_precip.get_ylim()
    ax_precip.set_ylim(y_min, y_max + (y_max - y_min) * 0.4)

    fig.tight_layout()
    
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=300, bbox_inches='tight', facecolor='white', edgecolor='none')
    buf.seek(0)
    result = buf.read()
    plt.close(fig)
    return result

@app.post("/api/export_trend_figure")
async def export_trend_figure(request: ExportRequest):
    """Generate and return a publication-quality trend analysis figure."""
    loop = asyncio.get_event_loop()
    image_bytes = await loop.run_in_executor(executor, generate_trend_figure, request)
    
    return Response(
        content=image_bytes,
        media_type="image/png",
        headers={
            "Content-Disposition": "attachment; filename=Trend_Analysis_Figure.png"
        }
    )

def generate_publication_figure(data: ExportRequest) -> bytes:
    """Generate a publication-quality composite figure."""
    n_maps = len(data.previews)
    
    # Figure layout depends on whether maps are included
    if n_maps > 0:
        # WITH maps: top row = spatial maps, bottom = time series chart
        fig_width = max(7.5, n_maps * 2.5)
        fig = plt.figure(figsize=(fig_width, 8))
        gs = gridspec.GridSpec(2, 1, height_ratios=[0.42, 0.58], hspace=0.35)
        
        # ============ TOP ROW: Spatial Distribution Maps ============
        gs_maps = gridspec.GridSpecFromSubplotSpec(1, n_maps, subplot_spec=gs[0], wspace=0.15)
        panel_labels = [chr(ord('a') + i) for i in range(n_maps)]
        sorted_previews = sorted(data.previews, key=lambda x: x['year'])
        
        for i, preview in enumerate(sorted_previews):
            ax = fig.add_subplot(gs_maps[0, i])
            try:
                svg_string = base64.b64decode(preview['base64']).decode('utf-8')
                import re
                match = re.search(r'image/png;base64,([^"\'\>]+)', svg_string)
                if match:
                    png_base64 = re.sub(r'\s+', '', match.group(1))
                    png_bytes = base64.b64decode(png_base64)
                    img = plt.imread(io.BytesIO(png_bytes), format='png')
                    ax.imshow(img, aspect='equal')
                else:
                    ax.text(0.5, 0.5, f'No Map Data\n{preview["year"]}', ha='center', va='center',
                           transform=ax.transAxes, fontsize=12)
            except Exception as e:
                print(f"Error parsing map for {preview['year']}: {e}")
                ax.text(0.5, 0.5, f'FVC\n{preview["year"]}', ha='center', va='center',
                       transform=ax.transAxes, fontsize=12)
            
            ax.set_title(f'({panel_labels[i]}) {preview["year"]}', fontweight='bold', pad=8)
            ax.set_xticks([])
            ax.set_yticks([])
            for spine in ax.spines.values():
                spine.set_linewidth(0.8)
        
        # Add shared colorbar for maps
        cbar_ax = fig.add_axes([0.92, 0.55, 0.015, 0.35])
        vmin, vmax = (-1.0, 1.0) if data.metricType == 'NDVI' else (0.0, 1.0)
        sm = plt.cm.ScalarMappable(cmap='RdYlGn', norm=plt.Normalize(vmin=vmin, vmax=vmax))
        sm.set_array([])
        cbar = fig.colorbar(sm, cax=cbar_ax)
        cbar.set_label(data.metricType, fontsize=10, fontweight='bold')
        cbar.ax.tick_params(labelsize=8)
        
        # Time series chart goes in the bottom subplot
        chart_label = chr(ord('a') + n_maps)
        ax_metric = fig.add_subplot(gs[1])
    else:
        # WITHOUT maps: only time series chart, clean and compact
        fig_width = max(7.5, len(data.timeline) * 0.6)
        fig = plt.figure(figsize=(fig_width, 5))
        chart_label = 'a'
        ax_metric = fig.add_subplot(111)
    
    # ============ Time Series Chart ============
    years = data.timeline
    x = np.arange(len(years))
    
    # Metric line (primary Y-axis)
    color_metric = '#2E7D32'
    line_metric = ax_metric.plot(x, data.metricSeries, 'o-', color=color_metric, 
                           label=f'Mean {data.metricType}', linewidth=2.0, markersize=8,
                           markerfacecolor='white', markeredgecolor=color_metric, 
                           markeredgewidth=1.8, zorder=5)
    
    ax_metric.set_xlabel('Year', fontweight='bold')
    ax_metric.set_ylabel(f'Mean {data.metricType}', color=color_metric, fontweight='bold')
    ax_metric.tick_params(axis='y', labelcolor=color_metric)
    ax_metric.set_xticks(x)
    ax_metric.set_xticklabels(years, rotation=45 if len(years) > 10 else 0, ha='right' if len(years) > 10 else 'center')
    
    # Add Metric value annotations
    for xi, yi in zip(x, data.metricSeries):
        if yi is not None:
            ax_metric.annotate(f'{yi:.3f}', (xi, yi), textcoords="offset points",
                          xytext=(0, 12), ha='center', fontsize=7 if len(years) > 10 else 8, color=color_metric,
                          fontweight='bold')
    
    # Temperature (secondary Y-axis)
    ax_temp = ax_metric.twinx()
    color_temp = '#D32F2F'
    line_temp = ax_temp.plot(x, data.tempSeries, 's--', color=color_temp,
                            label='Mean Temperature', linewidth=1.5, markersize=7,
                            markerfacecolor='white', markeredgecolor=color_temp,
                            markeredgewidth=1.5, zorder=4)
    ax_temp.set_ylabel('Temperature (°C)', color=color_temp, fontweight='bold')
    ax_temp.tick_params(axis='y', labelcolor=color_temp)
    
    # Precipitation (third Y-axis, offset)
    ax_precip = ax_metric.twinx()
    ax_precip.spines['right'].set_position(('outward', 60))
    color_precip = '#1565C0'
    line_precip = ax_precip.plot(x, data.precipSeries, 'D-.', color=color_precip,
                                 label='Annual Precipitation', linewidth=1.5, markersize=6,
                                 markerfacecolor='white', markeredgecolor=color_precip,
                                 markeredgewidth=1.5, zorder=3)
    ax_precip.set_ylabel('Precipitation (mm)', color=color_precip, fontweight='bold')
    ax_precip.tick_params(axis='y', labelcolor=color_precip)
    
    # Combined legend
    lines = line_metric + line_temp + line_precip
    labels = [l.get_label() for l in lines]
    ax_metric.legend(lines, labels, loc='upper right', framealpha=0.9, 
                  edgecolor='#cccccc', fancybox=False, fontsize=7)
    
    # Grid and styling
    ax_metric.grid(True, linestyle='--', alpha=0.3, color='gray')
    ax_metric.set_title(f'({chart_label}) Temporal Variation of {data.metricType} and Climate Factors',
                     fontweight='bold', pad=12)
    
    # Y-axis range padding
    valid_metrics = [v for v in data.metricSeries if v is not None]
    if valid_metrics:
        metric_min, metric_max = min(valid_metrics), max(valid_metrics)
        metric_margin = max((metric_max - metric_min) * 0.25, 0.02)
        ax_metric.set_ylim(metric_min - metric_margin, metric_max + metric_margin)
    
    # Adjust layout
    fig.subplots_adjust(left=0.08, right=0.82, top=0.95, bottom=0.12 if len(years) > 10 else 0.08)
    
    # Save to buffer as high-res PNG (300 DPI)
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=300, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    buf.seek(0)
    result = buf.read()
    plt.close(fig)
    return result

@app.post("/api/export_figure")
async def export_figure(request: ExportRequest):
    """Generate and return a publication-quality composite figure."""
    loop = asyncio.get_event_loop()
    image_bytes = await loop.run_in_executor(executor, generate_publication_figure, request)
    
    return Response(
        content=image_bytes,
        media_type="image/png",
        headers={
            "Content-Disposition": "attachment; filename=FVC_Temporal_Analysis_Publication.png"
        }
    )

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
