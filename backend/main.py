from fastapi import FastAPI, File, UploadFile, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
import uvicorn
import rasterio
from rasterio.merge import merge
import numpy as np
import os
import tempfile
import json
from typing import Optional, List
import ee
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
    'font.family': 'serif',
    'font.serif': ['Times New Roman', 'DejaVu Serif', 'serif'],
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    """Downsample a 2D array so its longest side is at most max_pixels.
    This dramatically speeds up matplotlib rendering without affecting FVC accuracy."""
    h, w = arr.shape
    if max(h, w) <= max_pixels:
        return arr
    scale = max_pixels / max(h, w)
    new_h, new_w = max(1, int(h * scale)), max(1, int(w * scale))
    # Use stride-based downsampling (nearest neighbor) - extremely fast
    row_idx = np.linspace(0, h - 1, new_h, dtype=int)
    col_idx = np.linspace(0, w - 1, new_w, dtype=int)
    return arr[np.ix_(row_idx, col_idx)]

def calculate_mosaic_metric(tif_paths: List[str], metric_type: str = "FVC"):
    """CPU-bound: merge tiles, compute mean Metric, generate preview."""
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
        
        # Downsample for preview rendering (huge speedup for large images)
        vis_small = downsample_array(vis_array, max_pixels=800)

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
        plt.close(fig)
        t3 = time.time()
        print(f"  [perf] matplotlib render: {t3 - t2:.2f}s")
        print(f"  [perf] TOTAL: {t3 - t0:.2f}s (original pixels: {height}x{width}, preview: {vis_small.shape[0]}x{vis_small.shape[1]})")

        return round(mean_val, 4), bbox, preview_b64
    finally:
        for src in src_files_to_mosaic:
            src.close()

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
        
        # Run the CPU-heavy mosaic+render in a thread pool to avoid blocking the event loop
        loop = asyncio.get_event_loop()
        mean_val, bbox, preview_b64 = await loop.run_in_executor(
            executor, calculate_mosaic_metric, temp_files, metric_type
        )
        
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
    bbox: str = Form(...)
):
    """Extracts climate data for specific years from GEE or uses fallback."""
    year_list = sorted([y.strip() for y in years.split(",") if y.strip()])
    bbox_list = [float(x.strip()) for x in bbox.split(",")][:4]
    
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
                start_date = f"{int(y)}-01-01"
                end_date = f"{int(y)+1}-01-01"
                
                annual = era5.filterBounds(region).filterDate(start_date, end_date)
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

class ExportRequest(BaseModel):
    timeline: List[str]
    metricSeries: List[float]
    metricType: str
    tempSeries: List[float]
    precipSeries: List[float]
    previews: List[dict]  # [{year: str, base64: str}]

def generate_publication_figure(data: ExportRequest) -> bytes:
    """Generate a publication-quality composite figure."""
    n_maps = len(data.previews)
    
    # Figure layout: top row = spatial maps, bottom = time series chart
    # Journal standard: single column ~3.5in, double column ~7in
    fig_width = max(7.5, n_maps * 2.5)
    fig = plt.figure(figsize=(fig_width, 8))
    
    # GridSpec: top 45% for maps, bottom 55% for time series
    gs = gridspec.GridSpec(2, 1, height_ratios=[0.42, 0.58], hspace=0.35)
    
    # ============ TOP ROW: Spatial Distribution Maps ============
    gs_maps = gridspec.GridSpecFromSubplotSpec(1, n_maps, subplot_spec=gs[0], wspace=0.15)
    
    panel_labels = [chr(ord('a') + i) for i in range(n_maps)]
    
    sorted_previews = sorted(data.previews, key=lambda x: x['year'])
    
    for i, preview in enumerate(sorted_previews):
        ax = fig.add_subplot(gs_maps[0, i])
        
        try:
            svg_string = base64.b64decode(preview['base64']).decode('utf-8')
            # The SVG contains an embedded PNG of the spatial map. Extract it using regex.
            # Matplotlib wraps base64 strings across multiple lines, so we must allow whitespaces (\s) inside the match
            import re
            match = re.search(r'image/png;base64,([^"\'\>]+)', svg_string)
            if match:
                # Remove any newlines or spaces from the multiline base64 string
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
    if n_maps > 0:
        cbar_ax = fig.add_axes([0.92, 0.55, 0.015, 0.35])
        vmin, vmax = (-1.0, 1.0) if data.metricType == 'NDVI' else (0.0, 1.0)
        sm = plt.cm.ScalarMappable(cmap='RdYlGn', norm=plt.Normalize(vmin=vmin, vmax=vmax))
        sm.set_array([])
        cbar = fig.colorbar(sm, cax=cbar_ax)
        cbar.set_label(data.metricType, fontsize=10, fontweight='bold')
        cbar.ax.tick_params(labelsize=8)
    
    # ============ BOTTOM: Time Series Chart ============
    last_label = chr(ord('a') + n_maps)
    ax_metric = fig.add_subplot(gs[1])
    
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
    ax_metric.set_xticklabels(years)
    
    # Add Metric value annotations
    for xi, yi in zip(x, data.metricSeries):
        if yi is not None:
            ax_metric.annotate(f'{yi:.3f}', (xi, yi), textcoords="offset points",
                          xytext=(0, 12), ha='center', fontsize=8, color=color_metric,
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
    ax_metric.legend(lines, labels, loc='upper left', framealpha=0.9, 
                  edgecolor='#cccccc', fancybox=False)
    
    # Grid and styling
    ax_metric.grid(True, linestyle='--', alpha=0.3, color='gray')
    ax_metric.set_title(f'({last_label}) Temporal Variation of {data.metricType} and Climate Factors',
                     fontweight='bold', pad=12)
    
    # Y-axis range padding
    metric_min, metric_max = min(data.metricSeries), max(data.metricSeries)
    metric_margin = max((metric_max - metric_min) * 0.25, 0.02)
    ax_metric.set_ylim(metric_min - metric_margin, metric_max + metric_margin)
    
    # Adjust layout
    fig.subplots_adjust(left=0.08, right=0.82, top=0.95, bottom=0.08)
    
    # Save to buffer as high-res TIFF-compatible PNG (300 DPI)
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
