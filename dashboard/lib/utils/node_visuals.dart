import 'package:flutter/material.dart';
import '../models/network_node.dart';

IconData getNodeIcon(NetworkNode node) {
  switch (node.type) {
    case NodeType.P:
      return Icons.hub;
    case NodeType.PE:
      return Icons.router;
    case NodeType.CE:
      return Icons.business;
    case NodeType.RR:
      return Icons.sync_alt;
    case NodeType.Device:
      return Icons.devices; // Icon for end devices
  }
}

Color getNodeColor(NetworkNode node) {
  switch (node.type) {
    case NodeType.P:
      return const Color(0xFF0D47A1); // Dark blue
    case NodeType.PE:
      return const Color(0xFF1976D2); // Medium blue
    case NodeType.CE:
      return const Color(0xFF42A5F5); // Light blue
    case NodeType.RR:
      return const Color(0xFF6A1B9A); // Purple for Route Reflector
    case NodeType.Device:
      return const Color(0xFF66BB6A); // Green for devices
  }
}
