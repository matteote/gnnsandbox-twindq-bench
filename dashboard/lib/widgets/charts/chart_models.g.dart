// GENERATED CODE - DO NOT MODIFY BY HAND

part of 'chart_models.dart';

// **************************************************************************
// JsonSerializableGenerator
// **************************************************************************

ChartDataPoint _$ChartDataPointFromJson(Map<String, dynamic> json) =>
    ChartDataPoint(
      timestamp: json['timestamp'] as String,
      value: (json['value'] as num).toDouble(),
      label: json['label'] as String?,
      series: json['series'] as String?,
    );

Map<String, dynamic> _$ChartDataPointToJson(ChartDataPoint instance) =>
    <String, dynamic>{
      'timestamp': instance.timestamp,
      'value': instance.value,
      'label': instance.label,
      'series': instance.series,
    };

ChartAnnotation _$ChartAnnotationFromJson(Map<String, dynamic> json) =>
    ChartAnnotation(
      timestamp: json['timestamp'] as String,
      text: json['text'] as String,
      type: json['type'] as String,
    );

Map<String, dynamic> _$ChartAnnotationToJson(ChartAnnotation instance) =>
    <String, dynamic>{
      'timestamp': instance.timestamp,
      'text': instance.text,
      'type': instance.type,
    };

ChartToolData _$ChartToolDataFromJson(Map<String, dynamic> json) =>
    ChartToolData(
      title: json['title'] as String,
      data: (json['data'] as List<dynamic>)
          .map((e) => ChartDataPoint.fromJson(e as Map<String, dynamic>))
          .toList(),
      chartType: json['chartType'] as String? ?? 'line',
      xAxisLabel: json['xAxisLabel'] as String?,
      yAxisLabel: json['yAxisLabel'] as String?,
      color: json['color'] as String?,
      showGrid: json['showGrid'] as bool? ?? true,
      showLegend: json['showLegend'] as bool? ?? true,
      timeFormat: json['timeFormat'] as String?,
      valueFormat: json['valueFormat'] as String?,
      height: (json['height'] as num?)?.toDouble() ?? 400,
      width: (json['width'] as num?)?.toDouble(),
      aggregation: json['aggregation'] as String? ?? 'none',
      aggregationFunction: json['aggregationFunction'] as String? ?? 'average',
      annotations: (json['annotations'] as List<dynamic>?)
          ?.map((e) => ChartAnnotation.fromJson(e as Map<String, dynamic>))
          .toList(),
    );

Map<String, dynamic> _$ChartToolDataToJson(ChartToolData instance) =>
    <String, dynamic>{
      'title': instance.title,
      'data': instance.data,
      'chartType': instance.chartType,
      'xAxisLabel': instance.xAxisLabel,
      'yAxisLabel': instance.yAxisLabel,
      'color': instance.color,
      'showGrid': instance.showGrid,
      'showLegend': instance.showLegend,
      'timeFormat': instance.timeFormat,
      'valueFormat': instance.valueFormat,
      'height': instance.height,
      'width': instance.width,
      'aggregation': instance.aggregation,
      'aggregationFunction': instance.aggregationFunction,
      'annotations': instance.annotations,
    };
