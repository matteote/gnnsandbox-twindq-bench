import 'package:flutter/material.dart';

/// Summary of a deployed VyosInfrastructure custom resource.
class VyosInfrastructureInfo {
  final String name;
  final String phase;
  final String message;
  final int routerCount;
  final int networkCount;
  final int deviceCount;

  const VyosInfrastructureInfo({
    required this.name,
    required this.phase,
    required this.message,
    required this.routerCount,
    required this.networkCount,
    required this.deviceCount,
  });

  factory VyosInfrastructureInfo.fromJson(Map<String, dynamic> json) {
    return VyosInfrastructureInfo(
      name:         json['name']          as String? ?? '',
      phase:        json['phase']         as String? ?? 'Unknown',
      message:      json['message']       as String? ?? '',
      routerCount:  json['router_count']  as int?    ?? 0,
      networkCount: json['network_count'] as int?    ?? 0,
      deviceCount:  json['device_count']  as int?    ?? 0,
    );
  }

  /// Returns a color representing the current phase.
  Color get phaseColor {
    switch (phase.toLowerCase()) {
      case 'ready':
        return Colors.green;
      case 'creating':
      case 'processing':
      case 'validating':
        return Colors.orange;
      case 'error':
        return Colors.red;
      default:
        return Colors.grey;
    }
  }

  /// A compact summary line, e.g. "3 routers · 2 networks · 1 device"
  String get resourceSummary {
    final parts = <String>[];
    if (routerCount > 0) {
      parts.add('$routerCount router${routerCount == 1 ? '' : 's'}');
    }
    if (networkCount > 0) {
      parts.add('$networkCount network${networkCount == 1 ? '' : 's'}');
    }
    if (deviceCount > 0) {
      parts.add('$deviceCount device${deviceCount == 1 ? '' : 's'}');
    }
    return parts.isEmpty ? 'No resources' : parts.join(' · ');
  }
}
