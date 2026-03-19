import 'package:flutter/material.dart';
import 'package:google_maps_flutter/google_maps_flutter.dart';
import 'package:provider/provider.dart';
import '../../appstate.dart';
import '../../models/network_node.dart';
import 'node_details_dialog.dart';
import 'dart:ui' as ui;
import 'dart:async';
import 'dart:typed_data';

class GoogleMapsTopologyWidget extends StatefulWidget {
  final NetworkTopology topology;

  const GoogleMapsTopologyWidget({
    super.key,
    required this.topology,
  });

  @override
  State<GoogleMapsTopologyWidget> createState() => _GoogleMapsTopologyWidgetState();
}

class _GoogleMapsTopologyWidgetState extends State<GoogleMapsTopologyWidget> {
  GoogleMapController? _mapController;
  Set<Marker> _markers = {};
  Set<Polyline> _polylines = {};
  MapType _currentMapType = MapType.normal;

  // UK center coordinates
  static const LatLng _ukCenter = LatLng(54.5, -2.5);
  
  @override
  void initState() {
    super.initState();
    _createMarkersAndPolylines();
  }

  @override
  void didUpdateWidget(GoogleMapsTopologyWidget oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.topology != widget.topology) {
      _createMarkersAndPolylines();
    }
  }

  Future<void> _createMarkersAndPolylines() async {
    final Set<Marker> markers = {};
    final Set<Polyline> polylines = {};
    // final appState = Provider.of<Appstate>(context, listen: false);

    // Use topology nodes directly
    final nodes = widget.topology.nodes;
    final connections = widget.topology.connections;

    // Create markers for each node
    for (final node in nodes) {
      // Assign location based on node properties
      LatLng? position = _getNodePosition(node);
      
      // Skip nodes with no valid location
      if (position == null) {
        continue;
      }

      final Color statusColor = NetworkNode.getStatusColor(node.properties['status']);
      final bool hasIncident = _hasMatchingIncident(node);

      final String statusText = node.properties['status']?.toString() ?? 'unknown';
      final Color baseColor = node.isAnomaly ? Colors.red : statusColor;
      final BitmapDescriptor markerIcon = await _createCustomMarkerIconWithText(baseColor, node.isAnomaly || hasIncident, statusText);

      markers.add(
        Marker(
          markerId: MarkerId(node.id),
          position: position,
          infoWindow: InfoWindow(
            title: node.name,
            snippet: '${node.properties['role'] ?? 'Router'} - $statusText',
          ),
          icon: markerIcon,
          anchor: const Offset(0.5, 0.5), // Center the marker over coordinate
          onTap: () => _showNodeDetails(node),
        ),
      );
    }

    // Create polylines for connections
    for (final connection in connections) {
      final sourceNode = nodes.where((n) => n.id == connection.sourceId).firstOrNull;
      final targetNode = nodes.where((n) => n.id == connection.targetId).firstOrNull;

      if (sourceNode != null && targetNode != null) {
        final LatLng? sourcePos = _getNodePosition(sourceNode);
        final LatLng? targetPos = _getNodePosition(targetNode);

        if (sourcePos != null && targetPos != null) {
          polylines.add(
            Polyline(
              polylineId: PolylineId('${connection.sourceId}-${connection.targetId}'),
              points: [sourcePos, targetPos],
              color: const Color(0xFF1976D2),
              width: 3,
              geodesic: true,
            ),
          );
        }
      }
    }

    if (mounted) {
      setState(() {
        _markers = markers;
        _polylines = polylines;
      });
      
      // Auto-zoom to fit all markers if map is ready
      if (_mapController != null && markers.isNotEmpty) {
        // Add a small delay to ensure the map is fully rendered
        Future.delayed(const Duration(milliseconds: 500), _zoomToFit);
      }
    }
  }

  void _zoomToFit() {
    if (_mapController == null || _markers.isEmpty) return;

    double minLat = 90.0;
    double minLng = 180.0;
    double maxLat = -90.0;
    double maxLng = -180.0;

    for (final marker in _markers) {
      if (marker.position.latitude < minLat) minLat = marker.position.latitude;
      if (marker.position.latitude > maxLat) maxLat = marker.position.latitude;
      if (marker.position.longitude < minLng) minLng = marker.position.longitude;
      if (marker.position.longitude > maxLng) maxLng = marker.position.longitude;
    }

    if (minLat > maxLat) return;

    _mapController!.animateCamera(
      CameraUpdate.newLatLngBounds(
        LatLngBounds(
          southwest: LatLng(minLat, minLng),
          northeast: LatLng(maxLat, maxLng),
        ),
        100.0, // padding
      ),
    );
  }

  LatLng? _getNodePosition(NetworkNode node) {
    // Try to get location from properties
    if (node.properties.containsKey('location') && node.properties['location'] != null) {
      final location = node.properties['location'];
      if (location is Map && location.containsKey('latitude') && location.containsKey('longitude')) {
        try {
          final lat = _parseDouble(location['latitude']);
          final lng = _parseDouble(location['longitude']);
          if (lat != null && lng != null) {
            return LatLng(lat, lng);
          }
        } catch (e) {
          print('Error parsing location for node ${node.id}: $e');
        }
      }
    }
    
    // No valid location found
    return null;
  }
  
  double? _parseDouble(dynamic value) {
    if (value is double) return value;
    if (value is int) return value.toDouble();
    if (value is String) return double.tryParse(value);
    return null;
  }

  Future<BitmapDescriptor> _createCustomMarkerIconWithText(Color color, bool hasIncident, String text) async {
    final ui.PictureRecorder recorder = ui.PictureRecorder();
    final Canvas canvas = Canvas(recorder);
    final Paint paint = Paint()..color = color;
    
    // We need more height to accommodate the text.
    final double markerSize = 48.0;
    final double textHeight = 40.0; // Give plenty of height for large font
    final double width = 160.0; // Wide enough for text
    final double height = markerSize + textHeight + 10.0;
    
    // Center the circle horizontally
    final double circleX = width / 2;
    final double circleY = markerSize / 2;

    // Draw outer circle
    canvas.drawCircle(Offset(circleX, circleY), markerSize / 2, paint);

    // Draw white inner circle
    final Paint whitePaint = Paint()..color = Colors.white;
    canvas.drawCircle(Offset(circleX, circleY), markerSize / 2 - 4, whitePaint);

    // Draw status color center
    canvas.drawCircle(Offset(circleX, circleY), markerSize / 2 - 8, paint);

    // If has incident, draw red border
    if (hasIncident) {
      final Paint redPaint = Paint()
        ..color = Colors.red
        ..style = PaintingStyle.stroke
        ..strokeWidth = 4;
      canvas.drawCircle(Offset(circleX, circleY), markerSize / 2 - 2, redPaint);
    }
    
    // Draw text
    final TextSpan span = TextSpan(
      style: const TextStyle(
        color: Colors.black,
        fontSize: 24.0, // Large enough to be readable when scaled down by Maps
        fontWeight: FontWeight.bold,
      ),
      text: text,
    );
    
    final TextPainter textPainter = TextPainter(
      text: span,
      textAlign: TextAlign.center,
      textDirection: TextDirection.ltr,
    );
    textPainter.layout(minWidth: 0, maxWidth: width);
    
    // Center text below the circle
    final double textX = (width - textPainter.width) / 2;
    final double textY = markerSize + 4.0;
    
    // Draw a subtle background for text readability
    final Rect textBgRect = Rect.fromLTWH(
       textX - 8, textY - 4, textPainter.width + 16, textPainter.height + 8
    );
    final Paint bgPaint = Paint()..color = Colors.white;
    final RRect rRect = RRect.fromRectAndRadius(textBgRect, const Radius.circular(8));
    
    // Shadow for text bg
    canvas.drawShadow(Path()..addRRect(rRect), Colors.black, 4.0, true);
    canvas.drawRRect(rRect, bgPaint);
    
    textPainter.paint(canvas, Offset(textX, textY));

    final ui.Picture picture = recorder.endRecording();
    final ui.Image image = await picture.toImage(width.toInt(), height.toInt());
    final ByteData? bytes = await image.toByteData(format: ui.ImageByteFormat.png);

    return BitmapDescriptor.fromBytes(bytes!.buffer.asUint8List());
  }

  bool _hasMatchingIncident(NetworkNode node) {
    final appState = Provider.of<Appstate>(context, listen: false);
    final incidents = appState.incidents;

    for (final incident in incidents) {
      if (incident.state != 'resolved') {
        final incidentTitle = incident.title.toLowerCase().trim();
        final nodeName = node.name.toLowerCase().trim();
        if (incidentTitle == nodeName) {
          return true;
        }
      }
    }
    return false;
  }

  void _showNodeDetails(NetworkNode node) {
    final appState = Provider.of<Appstate>(context, listen: false);
    appState.getNodeDetails(node.id);

    showDialog(
      context: context,
      builder: (BuildContext context) => NodeDetailsDialog(node: node),
    );
  }

  void _onMapCreated(GoogleMapController controller) {
    _mapController = controller;
  }

  void _toggleMapType() {
    setState(() {
      _currentMapType = _currentMapType == MapType.normal
          ? MapType.satellite
          : _currentMapType == MapType.satellite
              ? MapType.hybrid
              : MapType.normal;
    });
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Container(
          width: double.infinity,
          height: 40,
          padding: const EdgeInsets.symmetric(vertical: 4.0, horizontal: 16.0),
          margin: const EdgeInsets.all(8.0),
          decoration: const BoxDecoration(
            color: Color(0xFFE3F2FD),
            borderRadius: BorderRadius.all(Radius.circular(8.0)),
          ),
          child: Center(
            child: Text(
              'Network Topology - UK Router Network',
              style: Theme.of(context).textTheme.titleMedium?.copyWith(
                    fontWeight: FontWeight.bold,
                    color: const Color(0xFF0D47A1),
                  ),
            ),
          ),
        ),
        Expanded(
          child: Stack(
                  children: [
                    GoogleMap(
                      onMapCreated: _onMapCreated,
                      initialCameraPosition: const CameraPosition(
                        target: _ukCenter,
                        zoom: 6.0,
                      ),
                      mapType: _currentMapType,
                      markers: _markers,
                      polylines: _polylines,
                      myLocationButtonEnabled: false,
                      zoomControlsEnabled: false,
                    ),
                    // Controls positioned in top-right corner
                    Positioned(
                      top: 16,
                      right: 16,
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          // Map type toggle
                          Container(
                            decoration: BoxDecoration(
                              color: Colors.white,
                              borderRadius: BorderRadius.circular(8),
                              boxShadow: [
                                BoxShadow(
                                  color: Colors.black.withOpacity(0.2),
                                  blurRadius: 4,
                                  offset: const Offset(0, 2),
                                ),
                              ],
                            ),
                            child: IconButton(
                              icon: const Icon(Icons.layers),
                              color: const Color(0xFF1976D2),
                              tooltip: 'Toggle Map Type',
                              onPressed: _toggleMapType,
                            ),
                          ),
                        ],
                      ),
                    ),
                    // Legend positioned in bottom-left corner
                    Positioned(
                      bottom: 16,
                      left: 16,
                      child: Container(
                        padding: const EdgeInsets.all(12),
                        decoration: BoxDecoration(
                          color: Colors.white,
                          borderRadius: BorderRadius.circular(8),
                          boxShadow: [
                            BoxShadow(
                              color: Colors.black.withOpacity(0.2),
                              blurRadius: 4,
                              offset: const Offset(0, 2),
                            ),
                          ],
                        ),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            const Text(
                              'Status',
                              style: TextStyle(
                                fontWeight: FontWeight.bold,
                                fontSize: 12,
                              ),
                            ),
                            const SizedBox(height: 4),
                            _buildLegendItem(Colors.green, 'Operational'),
                            _buildLegendItem(Colors.orange, 'Degraded'),
                            _buildLegendItem(Colors.red, 'Failed'),
                          ],
                        ),
                      ),
                    ),
                  ],
                ),
        ),
      ],
    );
  }

  Widget _buildLegendItem(Color color, String label) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 12,
            height: 12,
            decoration: BoxDecoration(
              color: color,
              shape: BoxShape.circle,
            ),
          ),
          const SizedBox(width: 6),
          Text(
            label,
            style: const TextStyle(fontSize: 11),
          ),
        ],
      ),
    );
  }

  @override
  void dispose() {
    _mapController?.dispose();
    super.dispose();
  }
}
