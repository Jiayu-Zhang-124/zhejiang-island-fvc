import React, { useEffect, useRef } from 'react';
import * as echarts from 'echarts';

const FVCChart = ({ data }) => {
    const chartRef = useRef(null);

    useEffect(() => {
        if (!chartRef.current || !data) return;

        const myChart = echarts.init(chartRef.current);

        // Build series array explicitly
        const seriesList = [
            {
                name: 'Precipitation (mm)',
                type: 'bar',
                yAxisIndex: 1,
                itemStyle: {
                    color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                        { offset: 0, color: 'rgba(74, 107, 255, 0.8)' },
                        { offset: 1, color: 'rgba(74, 107, 255, 0.2)' }
                    ]),
                    borderRadius: [4, 4, 0, 0]
                },
                data: data.precipSeries
            },
            {
                name: 'Temperature (°C)',
                type: 'line',
                yAxisIndex: 2,
                smooth: true,
                itemStyle: { color: '#f59e0b' },
                lineStyle: { width: 3, shadowColor: 'rgba(245, 158, 11, 0.5)', shadowBlur: 10 },
                data: data.tempSeries
            },
            {
                name: `${data.metricType} Index`,
                type: 'line',
                smooth: true,
                itemStyle: { color: '#10b981' },
                lineStyle: { width: 4, shadowColor: 'rgba(16, 185, 129, 0.5)', shadowBlur: 10 },
                areaStyle: {
                    color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                        { offset: 0, color: 'rgba(16, 185, 129, 0.3)' },
                        { offset: 1, color: 'rgba(16, 185, 129, 0.0)' }
                    ])
                },
                data: data.metricSeries
            }
        ];

        // Build legend names
        const legendNames = [
            `${data.metricType} Index`, 'Temperature (°C)', 'Precipitation (mm)'
        ];

        // Add Theil-Sen trend lines if trendData exists
        console.log('[FVCChart] trendData:', data.trendData);

        if (data.trendData) {
            if (data.trendData.metric && data.trendData.metric.trendLine) {
                console.log('[FVCChart] Adding metric trend line, data points:', data.trendData.metric.trendLine.length);
                legendNames.push(`${data.metricType} Trend`);
                seriesList.push({
                    name: `${data.metricType} Trend`,
                    type: 'line',
                    smooth: false,
                    symbol: 'none',
                    yAxisIndex: 0,
                    itemStyle: { color: '#065f46' },
                    lineStyle: { width: 2.5, type: 'dashed' },
                    data: data.trendData.metric.trendLine
                });
            }
            if (data.trendData.temperature && data.trendData.temperature.trendLine) {
                console.log('[FVCChart] Adding temperature trend line, data points:', data.trendData.temperature.trendLine.length);
                legendNames.push('Temperature Trend');
                seriesList.push({
                    name: 'Temperature Trend',
                    type: 'line',
                    yAxisIndex: 2,
                    smooth: false,
                    symbol: 'none',
                    itemStyle: { color: '#b45309' },
                    lineStyle: { width: 2.5, type: 'dashed' },
                    data: data.trendData.temperature.trendLine
                });
            }
        }

        console.log('[FVCChart] Total series count:', seriesList.length, 'Legend:', legendNames);

        // Auto-calculate Y-axis range for metric
        const metricValues = data.metricSeries.filter(v => v != null);
        let metricMin = Math.min(...metricValues);
        let metricMax = Math.max(...metricValues);
        // Include trend line values in range
        if (data.trendData && data.trendData.metric && data.trendData.metric.trendLine) {
            const trendVals = data.trendData.metric.trendLine;
            metricMin = Math.min(metricMin, ...trendVals);
            metricMax = Math.max(metricMax, ...trendVals);
        }
        const metricPadding = Math.max((metricMax - metricMin) * 0.2, 0.02);
        const yMin = Math.floor((metricMin - metricPadding) * 20) / 20;
        const yMax = Math.min(1, Math.ceil((metricMax + metricPadding) * 20) / 20);

        const option = {
            backgroundColor: 'transparent',
            tooltip: {
                trigger: 'axis',
                axisPointer: {
                    type: 'cross',
                    crossStyle: { color: '#5e6678' }
                },
                backgroundColor: 'rgba(22, 25, 32, 0.9)',
                borderColor: 'rgba(255, 255, 255, 0.1)',
                textStyle: { color: '#f0f3f9' }
            },
            legend: {
                data: legendNames,
                textStyle: { color: '#a1a9bc' },
                top: 0
            },
            grid: {
                left: '3%',
                right: '12%',
                bottom: '3%',
                containLabel: true
            },
            xAxis: [
                {
                    type: 'category',
                    data: data.timeline,
                    axisPointer: { type: 'shadow' },
                    axisLine: { lineStyle: { color: '#5e6678' } },
                    axisLabel: { color: '#a1a9bc' }
                }
            ],
            yAxis: [
                {
                    type: 'value',
                    name: `${data.metricType} Index`,
                    min: yMin,
                    max: yMax,
                    position: 'left',
                    axisLabel: { color: '#10b981' },
                    nameTextStyle: { color: '#10b981' },
                    splitLine: {
                        lineStyle: {
                            color: 'rgba(255, 255, 255, 0.05)',
                            type: 'dashed'
                        }
                    }
                },
                {
                    type: 'value',
                    name: 'Precipitation',
                    position: 'right',
                    min: 0,
                    axisLabel: { color: 'rgba(74, 107, 255, 0.8)' },
                    nameTextStyle: { color: 'rgba(74, 107, 255, 0.8)' },
                    splitLine: { show: false }
                },
                {
                    type: 'value',
                    name: 'Temperature',
                    position: 'right',
                    offset: 50,
                    scale: true,
                    axisLabel: { color: '#f59e0b' },
                    nameTextStyle: { color: '#f59e0b', padding: [0, 0, 0, 15] },
                    splitLine: { show: false }
                }
            ],
            series: seriesList
        };

        myChart.setOption(option);

        const handleResize = () => {
            myChart.resize();
        };
        window.addEventListener('resize', handleResize);

        return () => {
            myChart.dispose();
            window.removeEventListener('resize', handleResize);
        };
    }, [data]);

    return <div ref={chartRef} style={{ width: '100%', height: '100%', minHeight: '400px' }} />;
};

export default FVCChart;
