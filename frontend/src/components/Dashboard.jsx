import { useState, useEffect } from 'react';
import { Upload, FileDown, CheckCircle, AlertCircle, RefreshCw, Plus, Trash2, Layers } from 'lucide-react';
import './Dashboard.css';
import FVCChart from './FVCChart';

// Use environment variable for backend URL in production, or fallback to localhost during development
const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const Dashboard = () => {
    const [timeNodes, setTimeNodes] = useState([
        { id: 1, year: '2018', files: [], isProcessing: false, result: null }
    ]);
    const [geeKey, setGeeKey] = useState(null);
    const [isGeeKeyAutoDetected, setIsGeeKeyAutoDetected] = useState(false);
    const [isGlobalProcessing, setIsGlobalProcessing] = useState(false);
    const [progressInfo, setProgressInfo] = useState(null);
    const [finalResults, setFinalResults] = useState(null);
    const [selectedImage, setSelectedImage] = useState(null);
    const [zoomLevel, setZoomLevel] = useState(1);
    const [analysisType, setAnalysisType] = useState('FVC'); // Options: 'FVC', 'NDVI'

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
                console.warn("Could not check default GEE key status.");
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
        setTimeNodes([...timeNodes, { id: nextId, year: '', files: [], isProcessing: false, result: null }]);
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
            setTimeNodes(timeNodes.map(n => {
                if (n.id === id) {
                    // Attempt to extract year from first file if year is empty
                    let suggestedYear = n.year;
                    if (!suggestedYear) {
                        const match = fileArray[0].name.match(/(19|20)\d{2}/);
                        if (match) suggestedYear = match[0];
                    }
                    return { ...n, files: [...n.files, ...fileArray], year: suggestedYear };
                }
                return n;
            }));
        }
    };

    const processAllData = async () => {
        const validNodes = timeNodes.filter(n => n.files.length > 0 && n.year.trim() !== '');
        if (validNodes.length === 0) {
            alert("Please configure at least one time node with files and a year.");
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
            if (geeKey) formData.append('gee_key', geeKey);

            const response = await fetch(`${API_BASE}/api/climate`, {
                method: 'POST',
                body: formData,
            });

            if (!response.ok) throw new Error('Climate fetch failed');
            const climateData = await response.json();

            const orderedMetricSeries = climateData.timeline.map(timelineYear => {
                const matchingNode = successfulNodes.find(n => n.year === timelineYear);
                return matchingNode ? matchingNode.result.value : null;
            });

            setFinalResults({
                timeline: climateData.timeline,
                metricSeries: orderedMetricSeries,
                metricType: analysisType,
                tempSeries: climateData.tempSeries,
                precipSeries: climateData.precipSeries,
                geeStatus: climateData.geeStatus
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
                                    <>
                                        <CheckCircle className="icon-success" size={24} />
                                        <span className="file-name" style={{ fontSize: '0.8rem' }}>{geeKey.name}</span>
                                    </>
                                ) : isGeeKeyAutoDetected ? (
                                    <>
                                        <CheckCircle className="icon-success" size={24} />
                                        <span style={{ fontSize: '0.8rem', fontWeight: '600' }}>GEE Key Auto-detected</span>
                                    </>
                                ) : (
                                    <>
                                        <RefreshCw className="icon-muted" size={24} />
                                        <span style={{ fontSize: '0.8rem' }}>Upload GEE Key JSON</span>
                                    </>
                                )}
                            </div>
                        </label>
                    </div>
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

                            <label className={`upload-dropzone tile-zone ${node.files.length > 0 ? 'has-file' : ''}`}>
                                <input type="file" accept=".tif,.tiff" multiple onChange={(e) => handleFilesUpload(node.id, e)} hidden />
                                <div className="upload-content">
                                    <Layers className={node.files.length > 0 ? "icon-success" : "icon-muted"} size={28} />
                                    <span>
                                        {node.files.length > 0
                                            ? `${node.files.length} TIF Tiles Selected`
                                            : `Drag multiple TIF tiles here`}
                                    </span>
                                </div>
                            </label>

                            {node.result && !node.result.error && (
                                <div className="node-result-badge">
                                    Mosaic FVC: {(node.result.fvcValue * 100).toFixed(1)}%
                                </div>
                            )}
                        </div>
                    ))}
                </div>

                <button className="btn btn-secondary" onClick={addTimeNode} style={{ width: '100%', marginBottom: '1rem' }}>
                    <Plus size={18} /> Add Time Period
                </button>

                <button
                    className="btn btn-primary run-btn"
                    onClick={processAllData}
                    disabled={isGlobalProcessing || timeNodes.length === 0}
                >
                    {isGlobalProcessing ? (
                        <><RefreshCw className="spin" size={18} /> Processing Spatial Mosaic & GEE...</>
                    ) : (
                        'Analyze Temporal Series'
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
                            <div className="stat-card glass-effect">
                                <button
                                    className="btn btn-export"
                                    onClick={async () => {
                                        const previews = timeNodes
                                            .filter(n => n.result && !n.result.error && n.result.previewBase64)
                                            .sort((a, b) => a.year.localeCompare(b.year))
                                            .map(n => ({ year: n.year, base64: n.result.previewBase64 }));

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
                                                    previews
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
                                    📄 Export Publication Figure (300 DPI)
                                </button>
                            </div>
                        </div>

                        <div className="chart-container" style={{ minHeight: '350px' }}>
                            <FVCChart data={finalResults} />
                        </div>


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
                            <button className="close-modal-btn" onClick={() => setSelectedImage(null)}>✕</button>
                        </div>
                        <div className="image-zoom-controls">
                            <button onClick={() => setZoomLevel(z => Math.max(0.5, z - 0.25))}>➖ Zoom Out</button>
                            <span>{Math.round(zoomLevel * 100)}%</span>
                            <button onClick={() => setZoomLevel(z => Math.min(5, z + 0.25))}>➕ Zoom In</button>
                            <button onClick={() => setZoomLevel(1)}>Reset</button>
                        </div>
                        <div className="image-zoom-container">
                            <img
                                src={selectedImage.src}
                                alt={`${selectedImage.year} Zoomed`}
                                style={{ transform: `scale(${zoomLevel})` }}
                                className="zoomed-image"
                            />
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

export default Dashboard;
