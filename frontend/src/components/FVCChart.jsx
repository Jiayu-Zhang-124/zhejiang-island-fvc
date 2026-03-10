import React, { useEffect, useRef } from 'react';
import * as echarts from 'echarts';

const FVCChart = ({ data }) => {
    const chartRef = useRef(null);

    useEffect(() => {
        if (!chartRef.current || !data) return;

        const myChart = echarts.init(chartRef.current);

        // Premium ECharts Configuration
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
                data: [`${data.metricType} Index`, 'Temperature (°C)', 'Precipitation (mm)'],
                textStyle: { color: '#a1a9bc' },
                top: 0
            },
            grid: {
                left: '3%',
                right: '4%',
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
                    min: data.metricType === 'NDVI' ? -1 : 0,
                    max: 1,
                    interval: data.metricType === 'NDVI' ? 0.4 : 0.2,
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
                    name: 'Climate (Temp/Precip)',
                    axisLabel: { color: '#00d4ff' },
                    nameTextStyle: { color: '#00d4ff' },
                    splitLine: { show: false }
                }
            ],
            series: [
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
                    yAxisIndex: 1,
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
            ]
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
