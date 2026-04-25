import 'package:json_annotation/json_annotation.dart';

part 'chart_models.g.dart';

@JsonSerializable()
class ChartDataPoint {
  final String timestamp;
  final double value;
  final String? label;
  final String? series;

  ChartDataPoint({
    required this.timestamp,
    required this.value,
    this.label,
    this.series,
  });

  factory ChartDataPoint.fromJson(Map<String, dynamic> json) =>
      _$ChartDataPointFromJson(json);

  Map<String, dynamic> toJson() => _$ChartDataPointToJson(this);
}

@JsonSerializable()
class ChartAnnotation {
  final String timestamp;
  final String text;
  final String type; // 'point', 'line', 'range'

  ChartAnnotation({
    required this.timestamp,
    required this.text,
    required this.type,
  });

  factory ChartAnnotation.fromJson(Map<String, dynamic> json) =>
      _$ChartAnnotationFromJson(json);

  Map<String, dynamic> toJson() => _$ChartAnnotationToJson(this);
}

@JsonSerializable()
class ChartToolData {
  final String title;
  final List<ChartDataPoint> data;
  final String? chartType;
  final String? xAxisLabel;
  final String? yAxisLabel;
  final String? color;
  final bool? showGrid;
  final bool? showLegend;
  final String? timeFormat;
  final String? valueFormat;
  final double? height;
  final double? width;
  final String? aggregation;
  final String? aggregationFunction;
  final List<ChartAnnotation>? annotations;

  ChartToolData({
    required this.title,
    required this.data,
    this.chartType = 'line',
    this.xAxisLabel,
    this.yAxisLabel,
    this.color,
    this.showGrid = true,
    this.showLegend = true,
    this.timeFormat,
    this.valueFormat,
    this.height = 400,
    this.width,
    this.aggregation = 'none',
    this.aggregationFunction = 'average',
    this.annotations,
  });

  factory ChartToolData.fromJson(Map<String, dynamic> json) =>
      _$ChartToolDataFromJson(json);

  Map<String, dynamic> toJson() => _$ChartToolDataToJson(this);
}

enum ChartType {
  line,
  area,
  bar,
  scatter,
}

extension ChartTypeExtension on ChartType {
  static ChartType fromString(String value) {
    switch (value.toLowerCase()) {
      case 'line':
        return ChartType.line;
      case 'area':
        return ChartType.area;
      case 'bar':
        return ChartType.bar;
      case 'scatter':
        return ChartType.scatter;
      default:
        return ChartType.line;
    }
  }
}
