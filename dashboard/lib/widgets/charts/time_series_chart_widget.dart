import 'package:flutter/material.dart';
import 'package:fl_chart/fl_chart.dart';
import 'dart:convert';

class TimeSeriesChartWidget extends StatelessWidget {
  final Map<String, dynamic> chartData;

  const TimeSeriesChartWidget({
    super.key,
    required this.chartData,
  });

  @override
  Widget build(BuildContext context) {
    try {
      final title = chartData['title'] as String? ?? 'Chart';
      final data = chartData['data'] as List<dynamic>? ?? [];
      final chartType = chartData['chartType'] as String? ?? 'line';
      final height = (chartData['height'] as num?)?.toDouble() ?? 400.0;
      final showGrid = chartData['showGrid'] as bool? ?? true;
      final showLegend = chartData['showLegend'] as bool? ?? true;
      final xAxisLabel = chartData['xAxisLabel'] as String?;
      final yAxisLabel = chartData['yAxisLabel'] as String?;
      final color = chartData['color'] as String? ?? '#3366CC';

      if (data.isEmpty) {
        return _buildErrorWidget('No data provided for chart');
      }

      // Parse data points
      final List<FlSpot> spots = [];
      final List<String> timeLabels = [];
      double minY = double.infinity;
      double maxY = double.negativeInfinity;

      for (int i = 0; i < data.length; i++) {
        final point = data[i] as Map<String, dynamic>;
        final timestamp = point['timestamp'] as String;
        final value = (point['value'] as num).toDouble();
        
        spots.add(FlSpot(i.toDouble(), value));
        timeLabels.add(_formatTimestamp(timestamp));
        
        if (value < minY) minY = value;
        if (value > maxY) maxY = value;
      }

      return Card(
        margin: const EdgeInsets.all(8.0),
        child: Padding(
          padding: const EdgeInsets.all(16.0),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Chart title
              Text(
                title,
                style: Theme.of(context).textTheme.titleLarge?.copyWith(
                  fontWeight: FontWeight.bold,
                ),
              ),
              const SizedBox(height: 16),
              
              // Chart
              SizedBox(
                height: height,
                child: _buildChart(
                  spots: spots,
                  timeLabels: timeLabels,
                  chartType: chartType,
                  showGrid: showGrid,
                  color: color,
                  minY: minY,
                  maxY: maxY,
                  xAxisLabel: xAxisLabel,
                  yAxisLabel: yAxisLabel,
                ),
              ),
              
              // Legend and labels
              if (showLegend || xAxisLabel != null || yAxisLabel != null)
                const SizedBox(height: 8),
              
              if (xAxisLabel != null)
                Center(
                  child: Text(
                    xAxisLabel,
                    style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                ),
            ],
          ),
        ),
      );
    } catch (e) {
      return _buildErrorWidget('Error rendering chart: $e');
    }
  }

  Widget _buildChart({
    required List<FlSpot> spots,
    required List<String> timeLabels,
    required String chartType,
    required bool showGrid,
    required String color,
    required double minY,
    required double maxY,
    String? xAxisLabel,
    String? yAxisLabel,
  }) {
    final chartColor = _parseColor(color);
    
    switch (chartType.toLowerCase()) {
      case 'line':
        return _buildLineChart(spots, timeLabels, showGrid, chartColor, minY, maxY);
      case 'area':
        return _buildAreaChart(spots, timeLabels, showGrid, chartColor, minY, maxY);
      case 'bar':
        return _buildBarChart(spots, timeLabels, showGrid, chartColor, minY, maxY);
      case 'scatter':
        return _buildScatterChart(spots, timeLabels, showGrid, chartColor, minY, maxY);
      default:
        return _buildLineChart(spots, timeLabels, showGrid, chartColor, minY, maxY);
    }
  }

  Widget _buildLineChart(
    List<FlSpot> spots,
    List<String> timeLabels,
    bool showGrid,
    Color color,
    double minY,
    double maxY,
  ) {
    return LineChart(
      LineChartData(
        gridData: FlGridData(show: showGrid),
        titlesData: FlTitlesData(
          leftTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              reservedSize: 60,
              getTitlesWidget: (value, meta) {
                return Text(
                  value.toStringAsFixed(1),
                  style: const TextStyle(fontSize: 12),
                );
              },
            ),
          ),
          bottomTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              reservedSize: 40,
              interval: (spots.length / 5).ceil().toDouble(),
              getTitlesWidget: (value, meta) {
                final index = value.toInt();
                if (index >= 0 && index < timeLabels.length) {
                  return Padding(
                    padding: const EdgeInsets.only(top: 8.0),
                    child: Text(
                      timeLabels[index],
                      style: const TextStyle(fontSize: 10),
                    ),
                  );
                }
                return const Text('');
              },
            ),
          ),
          topTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
          rightTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
        ),
        borderData: FlBorderData(show: true),
        minX: 0,
        maxX: (spots.length - 1).toDouble(),
        minY: minY * 0.9,
        maxY: maxY * 1.1,
        lineBarsData: [
          LineChartBarData(
            spots: spots,
            isCurved: true,
            color: color,
            barWidth: 2,
            dotData: const FlDotData(show: false),
            belowBarData: BarAreaData(show: false),
          ),
        ],
      ),
    );
  }

  Widget _buildAreaChart(
    List<FlSpot> spots,
    List<String> timeLabels,
    bool showGrid,
    Color color,
    double minY,
    double maxY,
  ) {
    return LineChart(
      LineChartData(
        gridData: FlGridData(show: showGrid),
        titlesData: FlTitlesData(
          leftTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              reservedSize: 60,
              getTitlesWidget: (value, meta) {
                return Text(
                  value.toStringAsFixed(1),
                  style: const TextStyle(fontSize: 12),
                );
              },
            ),
          ),
          bottomTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              reservedSize: 40,
              interval: (spots.length / 5).ceil().toDouble(),
              getTitlesWidget: (value, meta) {
                final index = value.toInt();
                if (index >= 0 && index < timeLabels.length) {
                  return Padding(
                    padding: const EdgeInsets.only(top: 8.0),
                    child: Text(
                      timeLabels[index],
                      style: const TextStyle(fontSize: 10),
                    ),
                  );
                }
                return const Text('');
              },
            ),
          ),
          topTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
          rightTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
        ),
        borderData: FlBorderData(show: true),
        minX: 0,
        maxX: (spots.length - 1).toDouble(),
        minY: minY * 0.9,
        maxY: maxY * 1.1,
        lineBarsData: [
          LineChartBarData(
            spots: spots,
            isCurved: true,
            color: color,
            barWidth: 2,
            dotData: const FlDotData(show: false),
            belowBarData: BarAreaData(
              show: true,
              color: color.withOpacity(0.3),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildBarChart(
    List<FlSpot> spots,
    List<String> timeLabels,
    bool showGrid,
    Color color,
    double minY,
    double maxY,
  ) {
    final barGroups = spots.map((spot) {
      return BarChartGroupData(
        x: spot.x.toInt(),
        barRods: [
          BarChartRodData(
            toY: spot.y,
            color: color,
            width: 16,
          ),
        ],
      );
    }).toList();

    return BarChart(
      BarChartData(
        gridData: FlGridData(show: showGrid),
        titlesData: FlTitlesData(
          leftTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              reservedSize: 60,
              getTitlesWidget: (value, meta) {
                return Text(
                  value.toStringAsFixed(1),
                  style: const TextStyle(fontSize: 12),
                );
              },
            ),
          ),
          bottomTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              reservedSize: 40,
              getTitlesWidget: (value, meta) {
                final index = value.toInt();
                if (index >= 0 && index < timeLabels.length) {
                  return Padding(
                    padding: const EdgeInsets.only(top: 8.0),
                    child: Text(
                      timeLabels[index],
                      style: const TextStyle(fontSize: 10),
                    ),
                  );
                }
                return const Text('');
              },
            ),
          ),
          topTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
          rightTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
        ),
        borderData: FlBorderData(show: true),
        minY: 0,
        maxY: maxY * 1.1,
        barGroups: barGroups,
      ),
    );
  }

  Widget _buildScatterChart(
    List<FlSpot> spots,
    List<String> timeLabels,
    bool showGrid,
    Color color,
    double minY,
    double maxY,
  ) {
    return LineChart(
      LineChartData(
        gridData: FlGridData(show: showGrid),
        titlesData: FlTitlesData(
          leftTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              reservedSize: 60,
              getTitlesWidget: (value, meta) {
                return Text(
                  value.toStringAsFixed(1),
                  style: const TextStyle(fontSize: 12),
                );
              },
            ),
          ),
          bottomTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              reservedSize: 40,
              interval: (spots.length / 5).ceil().toDouble(),
              getTitlesWidget: (value, meta) {
                final index = value.toInt();
                if (index >= 0 && index < timeLabels.length) {
                  return Padding(
                    padding: const EdgeInsets.only(top: 8.0),
                    child: Text(
                      timeLabels[index],
                      style: const TextStyle(fontSize: 10),
                    ),
                  );
                }
                return const Text('');
              },
            ),
          ),
          topTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
          rightTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
        ),
        borderData: FlBorderData(show: true),
        minX: 0,
        maxX: (spots.length - 1).toDouble(),
        minY: minY * 0.9,
        maxY: maxY * 1.1,
        lineBarsData: [
          LineChartBarData(
            spots: spots,
            isCurved: false,
            color: Colors.transparent,
            barWidth: 0,
            dotData: FlDotData(
              show: true,
              getDotPainter: (spot, percent, barData, index) {
                return FlDotCirclePainter(
                  radius: 4,
                  color: color,
                  strokeWidth: 2,
                  strokeColor: color.withOpacity(0.8),
                );
              },
            ),
            belowBarData: BarAreaData(show: false),
          ),
        ],
      ),
    );
  }

  Widget _buildErrorWidget(String message) {
    return Card(
      margin: const EdgeInsets.all(8.0),
      child: Padding(
        padding: const EdgeInsets.all(16.0),
        child: Row(
          children: [
            const Icon(Icons.error, color: Colors.red),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                message,
                style: const TextStyle(color: Colors.red),
              ),
            ),
          ],
        ),
      ),
    );
  }

  String _formatTimestamp(String timestamp) {
    try {
      final dateTime = DateTime.parse(timestamp);
      return '${dateTime.hour.toString().padLeft(2, '0')}:${dateTime.minute.toString().padLeft(2, '0')}';
    } catch (e) {
      return timestamp.length > 10 ? timestamp.substring(0, 10) : timestamp;
    }
  }

  Color _parseColor(String colorString) {
    try {
      if (colorString.startsWith('#')) {
        return Color(int.parse(colorString.substring(1), radix: 16) + 0xFF000000);
      }
      return const Color(0xFF3366CC); // Default blue
    } catch (e) {
      return const Color(0xFF3366CC); // Default blue
    }
  }
}
