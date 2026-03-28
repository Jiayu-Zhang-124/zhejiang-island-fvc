import { useState, useEffect, useRef } from 'react';
import { Upload, CheckCircle, AlertCircle, RefreshCw, Plus, Trash2, Layers } from 'lucide-react';
import './Dashboard.css';
import FVCChart from './FVCChart';

// Use environment variable for backend URL in production, or fallback to localhost during development
const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const Dashboard = () => {
    const [timeNodes, setTimeNodes] = useState([
        { id: 1, year: '', monthRange: '01-12', files: [], isProcessing: false, result: null }
    ]);
    const [geeKey, setGeeKey] = useState(null);
    const [isGeeKeyAutoDetected, setIsGeeKeyAutoDetected] = useState(false);
    const [isGlobalProcessing, setIsGlobalProcessing] = useState(false);
    const [progressInfo, setProgressInfo] = useState(null);
    const [finalResults, setFinalResults] = useState(null);
    const [selectedImage, setSelectedImage] = useState(null);
    const [zoomLevel, setZoomLevel] = useState(1);
    const [analysisType, setAnalysisType] = useState('FVC'); // Options: 'FVC', 'NDVI'
    const [exportWithMaps, setExportWithMaps] = useState(false); // Whether to include spatial maps in export
    const [globalClimateMonthRange, setGlobalClimateMonthRange] = useState({ start: '01', end: '12' });
    const [useGlobalMonthRange, setUseGlobalMonthRange] = useState(true);
    const [spatialTrendResult, setSpatialTrendResult] = useState(null);
    const [isSpatialProcessing, setIsSpatialProcessing] = useState(false);
    const [spatialProgress, setSpatialProgress] = useState(0);

    const [panOffset, setPanOffset] = useState({ x: 0, y: 0 });
    const [isPanning, setIsPanning] = useState(false);
    const [startPanPos, setStartPanPos] = useState({ x: 0, y: 0 });
    const nodesEndRef = useRef(null);



    useEffect(() => {
        if (nodesEndRef.current) {
            nodesEndRef.current.scrollIntoView({ behavior: 'smooth', block: 'end' });
        }
    }, [timeNodes.length]);

    useEffect(() => {
        // Check if GEE key is already present on the server
        const checkGee = async () => {
            try {
                const resp = await fetch(`${API_BASE}/api/check_gee`);
                const data = await resp.json();
                if (data.available) {
                    setIsGeeKeyAutoDetected(true);
                }
            } catch (err) {
                console.warn("Could not check default GEE key status.", err);
            }
        };
        checkGee();
    }, []);

    const handleGeeKeyUpload = (e) => {
        if (e.target.files && e.target.files[0]) {
            setGeeKey(e.target.files[0]);
        }
    };

    const addTimeNode = () => {
        const nextId = timeNodes.length > 0 ? Math.max(...timeNodes.map(n => n.id)) + 1 : 1;
        setTimeNodes([...timeNodes, { id: nextId, year: '', monthRange: '01-12', files: [], isProcessing: false, result: null }]);
    };

    const removeTimeNode = (id) => {
        setTimeNodes(timeNodes.filter(n => n.id !== id));
    };

    const updateTimeNodeYear = (id, year) => {
        setTimeNodes(timeNodes.map(n => n.id === id ? { ...n, year } : n));
    };

    const handleFilesUpload = (id, e) => {
        if (e.target.files && e.target.files.length > 0) {
            const fileArray = Array.from(e.target.files);
            
            // Check if this is the last node
            const isLastNode = timeNodes[timeNodes.length - 1].id === id;
            
            setTimeNodes(prevNodes => {
                const updatedNodes = prevNodes.map(n => {
                    if (n.id === id) {
                        let suggestedYear = n.year;
                        let suggestedMonthRange = n.monthRange || '01-12';
                        
                        const fileName = fileArray[0].name;
                        const yearMatch = fileName.match(/(19|20)\d{2}/);
                        if (yearMatch && !suggestedYear) suggestedYear = yearMatch[0];
                        
                        const monthMatch = fileName.match(/[_-](\d{1,2})[-_](\d{1,2})/);
                        if (monthMatch) {
                            const start = monthMatch[1].padStart(2, '0');
                            const end = monthMatch[2].padStart(2, '0');
                            suggestedMonthRange = `${start}-${end}`;
                        }
                        
                        return { ...n, files: [...n.files, ...fileArray], year: suggestedYear, monthRange: suggestedMonthRange };
                    }
                    return n;
                });
                
                // If the user uploaded to the LAST node, automatically append a new empty one
                if (isLastNode) {
                    const nextId = updatedNodes.length > 0 ? Math.max(...updatedNodes.map(n => n.id)) + 1 : 1;
                    return [...updatedNodes, { id: nextId, year: '', monthRange: '01-12', files: [], isProcessing: false, result: null }];
                }
                return updatedNodes;
            });
        }
    };



    const processAllData = async () => {
        const validNodes = timeNodes.filter(n => n.files.length > 0 && n.year.trim() !== '');
        if (validNodes.length === 0) {
            alert("Please configure at least one time node with files.");
            return;
        }

        setIsGlobalProcessing(true);
        setFinalResults(null);
        let allBboxes = [];
        let processingErrors = false;

        // Total steps = N mosaic nodes + 1 climate fetch
        const totalSteps = validNodes.length + 1;
        let completedSteps = 0;
        const updateProgress = (stepLabel) => {
            completedSteps++;
            setProgressInfo({
                current: completedSteps,
                total: totalSteps,
                percent: Math.round((completedSteps / totalSteps) * 100),
                label: stepLabel
            });
        };

        setProgressInfo({ current: 0, total: totalSteps, percent: 0, label: 'Initializing...' });

        // 1. Process each node SEQUENTIALLY so we can track progress
        const updatedNodes = [];
        for (const node of validNodes) {
            setProgressInfo(prev => ({ ...prev, label: `Mosaicking tiles for ${node.year}...` }));
            try {
                const formData = new FormData();
                formData.append('year', node.year);
                formData.append('metric_type', analysisType);
                node.files.forEach(file => {
                    formData.append('files', file);
                });

                const response = await fetch(`${API_BASE}/api/analyze_mosaic`, {
                    method: 'POST',
                    body: formData,
                    // Note: Browser fetch doesn't have a built-in timeout, but we can use AbortController if needed.
                    // For now, let's just improve the error logging.
                });

                if (!response.ok) {
                    const errorText = await response.text();
                    throw new Error(`Server returned ${response.status}: ${errorText}`);
                }
                const data = await response.json();
                allBboxes.push(data.bbox);
                updatedNodes.push({ ...node, result: data });
            } catch (err) {
                console.error(`Error processing node ${node.year}:`, err);
                processingErrors = true;
                updatedNodes.push({ ...node, result: { error: true, message: err.message } });
                alert(`Error processing ${node.year}: ${err.message}\n\nPlease check if your backend URL (${API_BASE}) is correct and accessible.`);
            }
            updateProgress(`Completed mosaic for ${node.year}`);
        }

        if (processingErrors) {
            alert("Some nodes failed to process mosaic. Check backend logs.");
        }

        // Update state to show individual FVC results
        setTimeNodes(timeNodes.map(n => {
            const updated = updatedNodes.find(u => u.id === n.id);
            return updated ? updated : n;
        }));

        // 2. Compute Global BBox
        const validBboxes = allBboxes.filter(b => b.length === 4);
        let globalBbox = [122.0, 29.5, 122.5, 30.0];
        if (validBboxes.length > 0) {
            globalBbox = [
                Math.min(...validBboxes.map(b => b[0])),
                Math.min(...validBboxes.map(b => b[1])),
                Math.max(...validBboxes.map(b => b[2])),
                Math.max(...validBboxes.map(b => b[3]))
            ];
        }

        // 3. Request Climate Data
        setProgressInfo(prev => ({ ...prev, label: 'Fetching climate data (GEE / Fallback)...' }));
        try {
            const successfulNodes = updatedNodes.filter(n => n.result && !n.result.error);
            const yearsList = successfulNodes.map(n => n.year).sort();

            const formData = new FormData();
            formData.append('years', yearsList.join(','));
            formData.append('bbox', globalBbox.join(','));
            
            // Send mapping of year to month_range
            const monthRanges = {};
            successfulNodes.forEach(n => {
                if (useGlobalMonthRange) {
                    monthRanges[n.year] = `${globalClimateMonthRange.start}-${globalClimateMonthRange.end}`;
                } else {
                    monthRanges[n.year] = n.monthRange || '01-12';
                }
            });
            formData.append('month_ranges', JSON.stringify(monthRanges));
            
            if (geeKey) formData.append('gee_key', geeKey);

            const response = await fetch(`${API_BASE}/api/climate`, {
                method: 'POST',
                body: formData,
            });

            if (!response.ok) throw new Error('Climate fetch failed');
            const climateData = await response.json();

            const orderedMetricSeries = climateData.timeline.map(timelineYear => {
                const matchingNode = successfulNodes.find(n => String(n.year) === String(timelineYear));
                return matchingNode ? matchingNode.result.value : null;
            });

            // 4. Trend Analysis (Theil-Sen + Mann-Kendall)
            let trendData = null;
            try {
                setProgressInfo(prev => ({ ...prev, label: 'Running Theil-Sen + Mann-Kendall trend analysis...' }));
                const trendResp = await fetch(`${API_BASE}/api/trend_analysis`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        timeline: climateData.timeline,
                        metricSeries: orderedMetricSeries,
                        metricType: analysisType,
                        tempSeries: climateData.tempSeries,
                        precipSeries: climateData.precipSeries
                    })
                });
                if (trendResp.ok) {
                    trendData = await trendResp.json();
                }
            } catch (trendErr) {
                console.warn('Trend analysis failed (non-critical):', trendErr);
            }

            setFinalResults({
                timeline: climateData.timeline,
                metricSeries: orderedMetricSeries,
                metricType: analysisType,
                tempSeries: climateData.tempSeries,
                precipSeries: climateData.precipSeries,
                geeStatus: climateData.geeStatus,
                trendData
            });

        } catch (err) {
            console.error("Climate fetch error:", err);
            alert("Failed to fetch climate data.");
        } finally {
            updateProgress('Done!');
            setIsGlobalProcessing(false);
            setProgressInfo(null);
        }
    };

    return (
        <div className="dashboard-grid animate-fade-in">
            {/* Sidebar: Mosaic & Controls */}
            <aside className="control-panel panel-scroll">
                <div className="glass-panel" style={{ marginBottom: '1.5rem', padding: '1rem' }}>
                    <h2 className="panel-title" style={{ fontSize: '1rem', border: 'none', padding: 0, margin: 0 }}>
                        Global Dependencies
                    </h2>
                    <div className="upload-section" style={{ marginTop: '0.5rem' }}>
                        <label className={`upload-dropzone compact-zone ${geeKey || isGeeKeyAutoDetected ? 'has-file' : ''}`}>
                            <input type="file" accept=".json" onChange={handleGeeKeyUpload} hidden />
                            <div className="upload-content">
                                {geeKey ? (
                                    <div style={{display: "flex", flexDirection: "column", alignItems: "center", gap: "4px"}}>
                                        <CheckCircle className="icon-success" size={24} />
                                        <span className="file-name" style={{ fontSize: '0.8rem' }}>{geeKey.name}</span>
                                    </div>
                                ) : isGeeKeyAutoDetected ? (
                                    <div style={{display: "flex", flexDirection: "column", alignItems: "center", gap: "4px"}}>
                                        <CheckCircle className="icon-success" size={24} />
                                        <span style={{ fontSize: '0.8rem', fontWeight: '600' }}>GEE Key Auto-detected</span>
                                    </div>
                                ) : (
                                    <div style={{display: "flex", flexDirection: "column", alignItems: "center", gap: "4px"}}>
                                        <RefreshCw className="icon-muted" size={24} />
                                        <span style={{ fontSize: '0.8rem' }}>Upload GEE Key JSON</span>
                                    </div>
                                )}
                            </div>
                        </label>
                    </div>
                </div>

                <div className="glass-panel" style={{ marginBottom: '1.5rem', padding: '1rem' }}>
                    <h2 className="panel-title" style={{ fontSize: '1rem', border: 'none', padding: 0, margin: 0, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        Climate Analysis Period
                        <input 
                            type="checkbox" 
                            checked={useGlobalMonthRange} 
                            onChange={(e) => setUseGlobalMonthRange(e.target.checked)} 
                            style={{ width: '16px', height: '16px' }}
                        />
                    </h2>
                    <p style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
                        {useGlobalMonthRange ? 'Using global range for all years' : 'Using filename detection'}
                    </p>
                    {useGlobalMonthRange && (
                        <div style={{ marginTop: '0.75rem', display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                            <select 
                                className="year-input" 
                                style={{ flex: 1, padding: '4px' }}
                                value={globalClimateMonthRange.start}
                                onChange={(e) => setGlobalClimateMonthRange(prev => ({...prev, start: e.target.value}))}
                            >
                                {[...Array(12)].map((_, i) => (
                                    <option key={i+1} value={(i+1).toString().padStart(2, '0')}>{i+1}月</option>
                                ))}
                            </select>
                            <span>至</span>
                            <select 
                                className="year-input" 
                                style={{ flex: 1, padding: '4px' }}
                                value={globalClimateMonthRange.end}
                                onChange={(e) => setGlobalClimateMonthRange(prev => ({...prev, end: e.target.value}))}
                            >
                                {[...Array(12)].map((_, i) => (
                                    <option key={i+1} value={(i+1).toString().padStart(2, '0')}>{i+1}月</option>
                                ))}
                            </select>
                        </div>
                    )}
                </div>

                <div className="glass-panel" style={{ marginBottom: '1.5rem', padding: '1rem' }}>
                    <h2 className="panel-title" style={{ fontSize: '1rem', border: 'none', padding: 0, margin: 0 }}>
                        Analysis Type
                    </h2>
                    <div className="upload-section" style={{ marginTop: '0.5rem' }}>
                        <select
                            className="year-input"
                            value={analysisType}
                            onChange={(e) => setAnalysisType(e.target.value)}
                            style={{ width: '100%', cursor: 'pointer', background: 'rgba(0,0,0,0.2)' }}
                        >
                            <option value="FVC">FVC (Fraction of Vegetation Cover)</option>
                            <option value="NDVI">NDVI (Normalized Difference Vegetation Index)</option>
                        </select>
                    </div>
                </div>

                <div className="nodes-container">
                    {timeNodes.map((node, index) => (
                        <div key={node.id} className="time-node-card glass-panel">
                            <div className="node-header">
                                <span className="node-index">Time Period {index + 1}</span>
                                <button className="icon-btn-danger" onClick={() => removeTimeNode(node.id)}>
                                    <Trash2 size={16} />
                                </button>
                            </div>

                            <div className="node-input-group">
                                <input
                                    type="text"
                                    placeholder="Year (e.g., 2018)"
                                    value={node.year}
                                    onChange={(e) => updateTimeNodeYear(node.id, e.target.value)}
                                    className="year-input"
                                />
                            </div>

                            <label className={`upload-dropzone tile-zone ${(node.files.length > 0 || (node.result && node.files.length === 0)) ? 'has-file' : ''}`}>
                                <input type="file" accept=".tif,.tiff" multiple onChange={(e) => handleFilesUpload(node.id, e)} hidden />
                                <div className="upload-content">
                                    <Layers className={(node.files.length > 0 || (node.result && node.files.length === 0)) ? "icon-success" : "icon-muted"} size={28} />
                                    <span>
                                        {node.result && node.files.length === 0
                                            ? `Restored from Workspace`
                                            : node.files.length > 0
                                                ? `${node.files.length} TIF Tiles Selected`
                                                : `Drag multiple TIF tiles here`}
                                    </span>
                                </div>
                            </label>

                            {((node.files.length > 0 && node.result && !node.result.error) || (node.result && node.files.length === 0 && !node.result.error)) && (
                                <div style={{ fontSize: '0.75rem', color: 'var(--accent-primary)', marginTop: '0.5rem', display: 'flex', justifyContent: 'flex-end' }}>
                                    <span style={{ fontWeight: 'bold' }}>
                                        {analysisType}: {(node.result.value * 100).toFixed(1)}%
                                    </span>
                                </div>
                            )}
                        </div>
                    ))}
                    <div ref={nodesEndRef} />
                </div>

                <div style={{ display: 'flex', gap: '0.8rem', marginBottom: '1rem', flexWrap: 'wrap' }}>
                    <button className="btn btn-secondary" onClick={addTimeNode} style={{ flex: '1', minWidth: '120px' }}>
                        <Plus size={18} /> Add Time
                    </button>
                </div>

                <button
                    className="btn btn-primary run-btn"
                    onClick={processAllData}
                    disabled={isGlobalProcessing || timeNodes.filter(n => n.files.length > 0 || n.result).length === 0}
                >
                    {isGlobalProcessing ? (
                        <span style={{display: "flex", alignItems: "center", gap: "8px"}}><RefreshCw className="spin" size={18} /> Processing Spatial Mosaic & GEE...</span>
                    ) : (
                        <span>Analyze Temporal Series</span>
                    )}
                </button>

                {/* Progress Bar */}
                {progressInfo && (
                    <div className="progress-container animate-fade-in">
                        <div className="progress-header">
                            <span className="progress-label">{progressInfo.label}</span>
                            <span className="progress-percent">{progressInfo.percent}%</span>
                        </div>
                        <div className="progress-track">
                            <div
                                className="progress-fill"
                                style={{ width: `${progressInfo.percent}%` }}
                            />
                        </div>
                        <span className="progress-steps">
                            Step {progressInfo.current} / {progressInfo.total}
                        </span>
                    </div>
                )}
            </aside>

            {/* Main Area: Visualization */}
            <section className="visualization-panel glass-panel">
                <h2 className="panel-title">Spatiotemporal Temporal Analysis Results</h2>

                {finalResults ? (
                    <div className="results-container animate-fade-in">
                        <div className="stats-row">
                            <div className="stat-card glass-effect">
                                <span className="stat-label">GEE Pipeline Status</span>
                                <span className={`stat-value ${finalResults.geeStatus.includes('Error') || finalResults.geeStatus.includes('Fail') ? 'text-error' : 'text-success'}`} style={{ fontSize: '1rem' }}>
                                    {finalResults.geeStatus}
                                </span>
                            </div>
                            <div className="stat-card glass-effect">
                                <span className="stat-label">Temporal Coverage</span>
                                <span className="stat-value text-gradient">{finalResults.timeline[0]} - {finalResults.timeline[finalResults.timeline.length - 1]}</span>
                            </div>
                            <div className="stat-card glass-effect" style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                                <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer', fontSize: '0.85rem' }}>
                                    <input
                                        type="checkbox"
                                        checked={exportWithMaps}
                                        onChange={(e) => setExportWithMaps(e.target.checked)}
                                        style={{ accentColor: 'var(--accent-primary)', width: '16px', height: '16px', cursor: 'pointer' }}
                                    />
                                    <span>导出时包含空间分布图</span>
                                </label>
                                <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                                    {exportWithMaps ? '⚠️ 年份较多时图片会很大' : '✅ 仅导出时间序列图表（推荐）'}
                                </span>
                                <button
                                    className="btn btn-export"
                                    onClick={async () => {
                                        const previews = exportWithMaps
                                            ? timeNodes
                                                .filter(n => n.result && !n.result.error && n.result.previewBase64)
                                                .sort((a, b) => a.year.localeCompare(b.year))
                                                .map(n => ({ year: n.year, base64: n.result.previewBase64 }))
                                            : [];

                                        try {
                                            const resp = await fetch(`${API_BASE}/api/export_figure`, {
                                                method: 'POST',
                                                headers: { 'Content-Type': 'application/json' },
                                                body: JSON.stringify({
                                                    timeline: finalResults.timeline,
                                                    metricSeries: finalResults.metricSeries,
                                                    metricType: finalResults.metricType,
                                                    tempSeries: finalResults.tempSeries,
                                                    precipSeries: finalResults.precipSeries,
                                                    previews,
                                                    trendData: finalResults.trendData || null
                                                })
                                            });
                                            if (!resp.ok) throw new Error('Export failed');
                                            const blob = await resp.blob();
                                            const url = URL.createObjectURL(blob);
                                            const a = document.createElement('a');
                                            a.href = url;
                                            a.download = `${finalResults.metricType}_Temporal_Analysis_Publication.png`;
                                            a.click();
                                            URL.revokeObjectURL(url);
                                        } catch (err) {
                                            alert('Export failed: ' + err.message);
                                        }
                                    }}
                                >
                                    <span>📄 导出论文插图 (300 DPI)</span>
                                </button>
                            </div>
                        </div>

                        <div className="chart-container" style={{ minHeight: '350px' }}>
                            <FVCChart data={finalResults} />
                        </div>

                        {/* Spatial Trend Analysis (Sen+MK) */}
                        {finalResults.trendData && finalResults.timeline.length >= 3 && (
                            <div className="glass-panel" style={{ padding: '1.25rem', marginTop: '1rem', border: '1px solid rgba(16, 185, 129, 0.3)' }}>
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
                                    <div>
                                        <h3 style={{ fontSize: '1rem', color: '#10b981', margin: 0, display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                                            <Layers size={18} /> Pixel-Based Spatial Trend Analysis
                                        </h3>
                                        <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', margin: '0.25rem 0 0 0' }}>
                                            Compute Sen+MK trend for every single pixel across the spatial map.
                                        </p>
                                    </div>
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.8rem' }}>
                                        <button
                                            className="btn btn-primary"
                                            disabled={isSpatialProcessing}
                                            onClick={async () => {
                                                setIsSpatialProcessing(true);
                                                setSpatialTrendResult(null);
                                                setSpatialProgress(0);

                                                // Start polling progress
                                                const pollInterval = setInterval(async () => {
                                                    try {
                                                        const pResp = await fetch(`${API_BASE}/api/spatial_progress`);
                                                        const pData = await pResp.json();
                                                        if (pData.status === 'processing') {
                                                            setSpatialProgress(pData.percent);
                                                        }
                                                    } catch (e) {
                                                        console.warn("Progress polling error", e);
                                                    }
                                                }, 1000);

                                                try {
                                                    const resp = await fetch(`${API_BASE}/api/spatial_trend`, {
                                                        method: 'POST',
                                                        headers: { 'Content-Type': 'application/json' },
                                                        body: JSON.stringify({
                                                            timeline: finalResults.timeline,
                                                            metricType: finalResults.metricType,
                                                            tempSeries: finalResults.tempSeries,
                                                            precipSeries: finalResults.precipSeries
                                                        })
                                                    });
                                                    if (!resp.ok) {
                                                        const errData = await resp.json();
                                                        throw new Error(errData.detail || 'Failed to compute spatial trend');
                                                    }
                                                    const data = await resp.json();
                                                    setSpatialTrendResult(data);
                                                    setSpatialProgress(100);
                                                } catch (err) {
                                                    alert('Spatial trend error: ' + err.message);
                                                } finally {
                                                    clearInterval(pollInterval);
                                                    setIsSpatialProcessing(false);
                                                }
                                            }}
                                            style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', width: 'fit-content' }}
                                        >
                                            {isSpatialProcessing ? <RefreshCw size={16} className="spin" /> : <Layers size={16} />}
                                            {isSpatialProcessing ? 'Processing Spatial Data...' : 'Generate Spatial Map'}
                                        </button>

                                        {isSpatialProcessing && (
                                            <div style={{ width: '100%', maxWidth: '300px' }}>
                                                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem', marginBottom: '4px', color: 'var(--text-muted)' }}>
                                                    <span>Calculating Trend Chunks...</span>
                                                    <span>{spatialProgress}%</span>
                                                </div>
                                                <div style={{ width: '100%', height: '6px', background: 'rgba(255,255,255,0.1)', borderRadius: '3px', overflow: 'hidden' }}>
                                                    <div 
                                                        style={{ 
                                                            width: `${spatialProgress}%`, 
                                                            height: '100%', 
                                                            background: 'linear-gradient(90deg, #10b981, #3b82f6)',
                                                            transition: 'width 0.3s ease'
                                                        }} 
                                                    />
                                                </div>
                                            </div>
                                        )}
                                    </div>
                                </div>
                                
                                {spatialTrendResult && spatialTrendResult.status === 'success' && (
                                    <div style={{ marginTop: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
                                        <div style={{ display: 'flex', gap: '1.5rem', alignItems: 'flex-start', flexWrap: 'wrap' }}>
                                            {/* Spatial Map Image */}
                                            <div style={{ flex: '1', minWidth: '300px', background: 'rgba(0,0,0,0.2)', padding: '1rem', borderRadius: '8px', textAlign: 'center' }}>
                                                <h4 style={{ fontSize: '0.9rem', color: 'var(--text-bright)', marginBottom: '0.5rem' }}>Spatial Trend Distribution</h4>
                                                <img 
                                                    src={`data:image/png;base64,${spatialTrendResult.mapBase64}`} 
                                                    alt="Spatial Trend Map" 
                                                    style={{ width: '100%', maxWidth: '500px', borderRadius: '4px', cursor: 'pointer' }}
                                                    className="clickable-image"
                                                    onClick={() => {
                                                        setSelectedImage({ src: `data:image/png;base64,${spatialTrendResult.mapBase64}`, year: 'Spatial Trend' });
                                                        setZoomLevel(1);
                                                    }}
                                                />
                                                <div style={{ marginTop: '0.5rem' }}>
                                                    <button className="btn btn-export" style={{ fontSize: '0.75rem', padding: '0.25rem 0.5rem' }} onClick={() => {
                                                        const link = document.createElement('a');
                                                        link.href = `data:image/png;base64,${spatialTrendResult.mapBase64}`;
                                                        link.download = `Spatial_Trend_Map.png`;
                                                        link.click();
                                                    }}>⬇️ Download Map</button>
                                                </div>
                                            </div>

                                            {/* Statistics Table */}
                                            <div style={{ flex: '2', minWidth: '400px', overflowX: 'auto' }}>
                                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
                                                    <h4 style={{ fontSize: '0.9rem', color: 'var(--text-bright)', margin: 0 }}>Area Statistics & Climate Correlation</h4>
                                                    <button className="btn btn-export" style={{ fontSize: '0.75rem', padding: '0.25rem 0.5rem' }} onClick={() => {
                                                        const headers = ["Trend Classification", "Area Percentage (%)", "Pixel Count", "Avg Temp Correlation (r)", "Avg Precip Correlation (r)"];
                                                        const rows = spatialTrendResult.statistics.map(s => [
                                                            s.trendClass, s.areaPercentage, s.pixelCount, s.avgTempCorr, s.avgPrecipCorr
                                                        ]);
                                                        const csvRows = [headers.join(","), ...rows.map(r => r.join(","))];
                                                        const blob = new Blob([csvRows.join("\n")], { type: 'text/csv;charset=utf-8;' });
                                                        const url = URL.createObjectURL(blob);
                                                        const link = document.createElement("a");
                                                        link.href = url;
                                                        link.download = `Spatial_Trend_Statistics.csv`;
                                                        link.click();
                                                        URL.revokeObjectURL(url);
                                                    }}>⬇️ Export Stats (CSV)</button>
                                                </div>
                                                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
                                                    <thead>
                                                        <tr style={{ borderBottom: '2px solid rgba(255,255,255,0.15)', background: 'rgba(0,0,0,0.2)' }}>
                                                            <th style={{ padding: '8px 12px', textAlign: 'left', color: 'var(--text-muted)' }}>Classification</th>
                                                            <th style={{ padding: '8px 12px', textAlign: 'right', color: 'var(--text-muted)' }}>Area %</th>
                                                            <th style={{ padding: '8px 12px', textAlign: 'right', color: 'var(--text-muted)' }}>Pixels</th>
                                                            <th style={{ padding: '8px 12px', textAlign: 'right', color: '#f59e0b' }}>Temp Corr (r)</th>
                                                            <th style={{ padding: '8px 12px', textAlign: 'right', color: '#3b82f6' }}>Precip Corr (r)</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody>
                                                        {spatialTrendResult.statistics.map((stat, idx) => {
                                                            const colorMap = {
                                                                "Highly Significant Increase": "#10b981",
                                                                "Significant Increase": "#34d399",
                                                                "No Significant Trend": "#9ca3af",
                                                                "Significant Decrease": "#f87171",
                                                                "Highly Significant Decrease": "#ef4444"
                                                            };
                                                            return (
                                                                <tr key={idx} style={{ borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
                                                                    <td style={{ padding: '8px 12px', fontWeight: 'bold', color: colorMap[stat.trendClass] || 'inherit' }}>{stat.trendClass}</td>
                                                                    <td style={{ padding: '8px 12px', textAlign: 'right' }}>{stat.areaPercentage}%</td>
                                                                    <td style={{ padding: '8px 12px', textAlign: 'right', color: 'var(--text-muted)' }}>{stat.pixelCount}</td>
                                                                    <td style={{ padding: '8px 12px', textAlign: 'right' }}>{stat.avgTempCorr.toFixed(4)}</td>
                                                                    <td style={{ padding: '8px 12px', textAlign: 'right' }}>{stat.avgPrecipCorr.toFixed(4)}</td>
                                                                </tr>
                                                            )
                                                        })}
                                                    </tbody>
                                                </table>
                                            </div>
                                        </div>
                                    </div>
                                )}
                            </div>
                        )}

                        {/* Spatial Mosaics Visualization Timeline */}
                        <div className="mosaic-timeline-section">
                            <h3 style={{ fontSize: '1rem', marginBottom: '1rem', color: 'var(--accent-primary)' }}>Spatial Distribution over Time ({finalResults.metricType} Range: {finalResults.metricType === 'NDVI' ? '-1.0 - 1.0' : '0.0 - 1.0'})</h3>
                            <div className="mosaic-grid">
                                {timeNodes
                                    .filter(node => node.result && !node.result.error && node.result.previewBase64)
                                    .sort((a, b) => a.year.localeCompare(b.year))
                                    .map(node => (
                                        <div key={node.id} className="mosaic-card glass-panel" style={{ padding: '0.75rem' }}>
                                            <div className="mosaic-year" style={{ textAlign: 'center', fontWeight: 'bold', marginBottom: '0.5rem' }}>{node.year}</div>
                                            <img
                                                src={`data:image/svg+xml;base64,${node.result.previewBase64}`}
                                                alt={`${node.year} ${finalResults.metricType} Map`}
                                                className="clickable-image"
                                                onClick={() => {
                                                    setSelectedImage({ src: `data:image/svg+xml;base64,${node.result.previewBase64}`, year: node.year });
                                                    setZoomLevel(1);
                                                }}
                                            />
                                        </div>
                                    ))
                                }
                            </div>
                        </div>
                    </div>
                ) : (
                    <div className="empty-state">
                        <AlertCircle className="icon-muted" size={48} />
                        <p>Upload multi-tile TIFs for different years to begin temporal comparison</p>
                        <span className="empty-hint">The system will mosaic each year's tiles automatically and generate visual distribution maps.</span>
                    </div>
                )}
            </section>

            {/* Image Zoom Modal */}
            {selectedImage && (
                <div className="image-modal-overlay" onClick={() => setSelectedImage(null)}>
                    <div className="image-modal-content" onClick={(e) => e.stopPropagation()}>
                        <div className="image-modal-header">
                            <h3>{selectedImage.year} {finalResults.metricType} Spatial Distribution</h3>
                            <button className="close-modal-btn" onClick={() => {
                                setSelectedImage(null);
                                setPanOffset({ x: 0, y: 0 });
                                setZoomLevel(1);
                            }}>✕</button>
                        </div>
                        <div className="image-zoom-controls">
                            <button onClick={() => setZoomLevel(z => Math.max(0.5, z - 0.25))}>➖ Zoom Out</button>
                            <span>{Math.round(zoomLevel * 100)}%</span>
                            <button onClick={() => setZoomLevel(z => Math.min(8, z + 0.25))}>➕ Zoom In</button>
                            <button onClick={() => {
                                setZoomLevel(1);
                                setPanOffset({ x: 0, y: 0 });
                            }}>Reset</button>
                        </div>
                        <div 
                            className="image-zoom-container"
                            style={{ cursor: isPanning ? 'grabbing' : 'grab' }}
                            onWheel={(e) => {
                                e.preventDefault();
                                e.stopPropagation();
                                const delta = e.deltaY < 0 ? 0.1 : -0.1;
                                const newZoom = Math.min(8, Math.max(0.5, zoomLevel + delta));
                                setZoomLevel(newZoom);
                            }}
                            onMouseDown={(e) => {
                                setIsPanning(true);
                                setStartPanPos({ x: e.clientX - panOffset.x, y: e.clientY - panOffset.y });
                            }}
                            onMouseMove={(e) => {
                                if (!isPanning) return;
                                setPanOffset({
                                    x: e.clientX - startPanPos.x,
                                    y: e.clientY - startPanPos.y
                                });
                            }}
                            onMouseUp={() => setIsPanning(false)}
                            onMouseLeave={() => setIsPanning(false)}
                        >
                            <img
                                src={selectedImage.src}
                                alt={`${selectedImage.year} Zoomed`}
                                style={{ 
                                    transform: `translate(${panOffset.x}px, ${panOffset.y}px) scale(${zoomLevel})`,
                                    transition: isPanning ? 'none' : 'transform 0.1s ease-out'
                                }}
                                className="zoomed-image"
                                draggable="false"
                            />
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

export default Dashboard;
