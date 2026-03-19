import 'dart:math';
import 'package:flutter/material.dart';
import '../../models/network_node.dart';
import '../../utils/node_visuals.dart';
import 'node_details_dialog.dart';

class LogicalTopologyWidget extends StatefulWidget {
  final NetworkTopology topology;

  const LogicalTopologyWidget({
    Key? key,
    required this.topology,
  }) : super(key: key);

  @override
  State<LogicalTopologyWidget> createState() => _LogicalTopologyWidgetState();
}

class _LogicalTopologyWidgetState extends State<LogicalTopologyWidget>
    with SingleTickerProviderStateMixin {
  late AnimationController _controller;
  final TransformationController _transformationController = TransformationController();
  Map<String, Offset> _positions = {};
  Map<String, Offset> _velocities = {};
  String? _draggedNodeId;
  bool _isSettled = false;
  
  // Physics parameters
  final double kRepulsion = 10000.0;
  final double kSpring = 0.05;
  final double kDamping = 0.85;
  final double restLength = 100.0;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
       vsync: this,
       duration: const Duration(milliseconds: 16), // ~60 FPS
    )..addListener(() {
      _applyForces();
    });
    
    _initializePositions();
    _precomputeLayout();
  }
  
  @override
  void didUpdateWidget(LogicalTopologyWidget oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.topology != oldWidget.topology) {
       // Only update if nodes have been added or removed
       final currentNodeIds = widget.topology.nodes.map((n) => n.id).toSet();
       final oldNodeIds = oldWidget.topology.nodes.map((n) => n.id).toSet();
       
       if (!currentNodeIds.containsAll(oldNodeIds) || !oldNodeIds.containsAll(currentNodeIds)) {
         // Nodes have changed - update positions for new nodes only
         _updatePositions();
         // Only re-settle if topology structure actually changed
         setState(() {
           _isSettled = false;
         });
         _precomputeLayout();
       }
       // If only connection or property changes, keep existing positions
    }
  }

  void _initializePositions() {
    final random = Random();
    _positions = {};
    _velocities = {};
    // Center cluster initially
    for (var node in widget.topology.nodes) {
      _positions[node.id] = Offset(
        random.nextDouble() * 400 + 100,
        random.nextDouble() * 400 + 100,
      );
      _velocities[node.id] = Offset.zero;
    }
  }
  
  void _updatePositions() {
    final random = Random();
    final currentNodeIds = widget.topology.nodes.map((n) => n.id).toSet();
    
    // Remove positions for deleted nodes
    _positions.removeWhere((id, _) => !currentNodeIds.contains(id));
    _velocities.removeWhere((id, _) => !currentNodeIds.contains(id));
    
    // Add positions for new nodes only
    for (var node in widget.topology.nodes) {
      if (!_positions.containsKey(node.id)) {
        _positions[node.id] = Offset(
          random.nextDouble() * 400 + 100,
          random.nextDouble() * 400 + 100,
        );
        _velocities[node.id] = Offset.zero;
      }
    }
  }

  Future<void> _precomputeLayout() async {
    // Run physics simulation silently for a number of iterations
    for (int i = 0; i < 300; i++) {
      _applyForcesWithoutSetState();
    }
    
    // Now show the settled layout and start the animation controller
    if (mounted) {
      setState(() {
        _isSettled = true;
      });
      // Start the continuous physics simulation for interactive dragging
      _controller.repeat();
    }
  }

  void _applyForcesWithoutSetState() {
    if (widget.topology.nodes.isEmpty) return;
    
    // Calculate repulsive forces between all nodes
    Map<String, Offset> forces = {};
    for (var node in widget.topology.nodes) {
       forces[node.id] = Offset.zero;
       
       // Repulsion from other nodes
       for (var other in widget.topology.nodes) {
          if (node.id != other.id) {
             final dx = _positions[node.id]!.dx - _positions[other.id]!.dx;
             final dy = _positions[node.id]!.dy - _positions[other.id]!.dy;
             final distSq = dx * dx + dy * dy;
             if (distSq > 0.1) {
                final dist = sqrt(distSq);
                final forceMag = kRepulsion / distSq;
                forces[node.id] = forces[node.id]! + Offset((dx / dist) * forceMag, (dy / dist) * forceMag);
             }
          }
       }
       
       // Attraction to center to keep graph on screen
       final dxCenter = 300 - _positions[node.id]!.dx;
       final dyCenter = 300 - _positions[node.id]!.dy;
       forces[node.id] = forces[node.id]! + Offset(dxCenter * 0.01, dyCenter * 0.01);
    }
    
    // Calculate attractive forces for edges (springs)
    for (var conn in widget.topology.connections) {
       if (_positions.containsKey(conn.sourceId) && _positions.containsKey(conn.targetId)) {
          final dx = _positions[conn.targetId]!.dx - _positions[conn.sourceId]!.dx;
          final dy = _positions[conn.targetId]!.dy - _positions[conn.sourceId]!.dy;
          final distSq = dx * dx + dy * dy;
          if (distSq > 0) {
             final dist = sqrt(distSq);
             // Hooke's Law: F = k * x
             final forceMag = kSpring * (dist - restLength);
             
             final force = Offset((dx / dist) * forceMag, (dy / dist) * forceMag);
             forces[conn.sourceId] = forces[conn.sourceId]! + force;
             forces[conn.targetId] = forces[conn.targetId]! - force; // Equal and opposite
          }
       }
    }
    
    // Update velocities and positions WITHOUT setState
    for (var node in widget.topology.nodes) {
       if (node.id == _draggedNodeId) {
          _velocities[node.id] = Offset.zero;
          continue;
       }
       _velocities[node.id] = (_velocities[node.id]! + forces[node.id]!) * kDamping;
       _positions[node.id] = _positions[node.id]! + _velocities[node.id]!;
    }
  }

  void _applyForces() {
    if (widget.topology.nodes.isEmpty) return;
    
    // Calculate repulsive forces between all nodes
    Map<String, Offset> forces = {};
    for (var node in widget.topology.nodes) {
       forces[node.id] = Offset.zero;
       
       // Repulsion from other nodes
       for (var other in widget.topology.nodes) {
          if (node.id != other.id) {
             final dx = _positions[node.id]!.dx - _positions[other.id]!.dx;
             final dy = _positions[node.id]!.dy - _positions[other.id]!.dy;
             final distSq = dx * dx + dy * dy;
             if (distSq > 0.1) {
                final dist = sqrt(distSq);
                final forceMag = kRepulsion / distSq;
                forces[node.id] = forces[node.id]! + Offset((dx / dist) * forceMag, (dy / dist) * forceMag);
             }
          }
       }
       
       // Attraction to center to keep graph on screen
       final dxCenter = 300 - _positions[node.id]!.dx;
       final dyCenter = 300 - _positions[node.id]!.dy;
       forces[node.id] = forces[node.id]! + Offset(dxCenter * 0.01, dyCenter * 0.01);
    }
    
    // Calculate attractive forces for edges (springs)
    for (var conn in widget.topology.connections) {
       if (_positions.containsKey(conn.sourceId) && _positions.containsKey(conn.targetId)) {
          final dx = _positions[conn.targetId]!.dx - _positions[conn.sourceId]!.dx;
          final dy = _positions[conn.targetId]!.dy - _positions[conn.sourceId]!.dy;
          final distSq = dx * dx + dy * dy;
          if (distSq > 0) {
             final dist = sqrt(distSq);
             // Hooke's Law: F = k * x
             final forceMag = kSpring * (dist - restLength);
             
             final force = Offset((dx / dist) * forceMag, (dy / dist) * forceMag);
             forces[conn.sourceId] = forces[conn.sourceId]! + force;
             forces[conn.targetId] = forces[conn.targetId]! - force; // Equal and opposite
          }
       }
    }
    
    // Update velocities and positions
    setState(() {
      for (var node in widget.topology.nodes) {
         if (node.id == _draggedNodeId) {
            _velocities[node.id] = Offset.zero;
            continue;
         }
         _velocities[node.id] = (_velocities[node.id]! + forces[node.id]!) * kDamping;
         _positions[node.id] = _positions[node.id]! + _velocities[node.id]!;
      }
    });
  }

  @override
  void dispose() {
    _controller.dispose();
    _transformationController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    // Show loading indicator while computing layout
    if (!_isSettled) {
      return Container(
        decoration: const BoxDecoration(
          color: Colors.white,
        ),
        child: const Center(
          child: CircularProgressIndicator(),
        ),
      );
    }
    
    return Container(
       decoration: const BoxDecoration(
         color: Colors.white, // White background
       ),
       clipBehavior: Clip.hardEdge, // Ensure content stays within bounds
       child: Stack(
         children: [
            InteractiveViewer(
              transformationController: _transformationController,
              constrained: false, // Important to allow panning effectively
              boundaryMargin: const EdgeInsets.all(4000), // Huge margins to let users drag far out
              minScale: 0.1,
              maxScale: 5.0,
              clipBehavior: Clip.hardEdge, // Clip content to prevent overflow
              child: SizedBox(
                   // Define a large canvas area
                   width: 5000, 
                   height: 5000,
                   child: Stack(
                      clipBehavior: Clip.none, // Also allow Stack children to draw freely if they slightly exceed
                      children: [
                        // Draw lines FIRST so they appear behind nodes
                        Positioned.fill(
                          child: CustomPaint(
                            painter: TopologyPainter(
                              topology: widget.topology,
                              positions: _positions,
                            ),
                          ),
                        ),
                        // Then draw the nodes on top
                        ...widget.topology.nodes.map((node) {
                           final pos = _positions[node.id] ?? Offset.zero;
                           return Positioned(
                              left: pos.dx - 20, // Center icon (40x40 roughly)
                              top: pos.dy - 20,
                              child: GestureDetector(
                                 onPanStart: (details) {
                                    setState(() {
                                       _draggedNodeId = node.id;
                                       _velocities[node.id] = Offset.zero;
                                    });
                                 },
                                 onPanUpdate: (details) {
                                    setState(() {
                                       final scale = _transformationController.value.getMaxScaleOnAxis();
                                       _positions[node.id] = _positions[node.id]! + (details.delta / scale);
                                       _velocities[node.id] = Offset.zero;
                                    });
                                 },
                                 onPanEnd: (details) {
                                    setState(() {
                                       _draggedNodeId = null;
                                    });
                                 },
                                 onPanCancel: () {
                                    setState(() {
                                       _draggedNodeId = null;
                                    });
                                 },
                                 onTap: () {
                                    showDialog(
                                       context: context,
                                       builder: (context) => NodeDetailsDialog(node: node),
                                    );
                                 },
                                 child: Column(
                                    children: [
                                       Stack(
                                         clipBehavior: Clip.none,
                                         children: [
                                           Container(
                                             decoration: BoxDecoration(
                                                shape: BoxShape.circle,
                                                boxShadow: node.isAnomaly
                                                  ? [
                                                      BoxShadow(
                                                        color: Colors.red.withOpacity(0.8),
                                                        blurRadius: 15,
                                                        spreadRadius: 5,
                                                      ),
                                                    ]
                                                  : [],
                                             ),
                                             child: CircleAvatar(
                                                backgroundColor: getNodeColor(node),
                                                radius: 20,
                                                child: Icon(
                                                   getNodeIcon(node),
                                                   color: Colors.white,
                                                   size: 24,
                                                ),
                                             ),
                                           ),
                                           Positioned(
                                             right: -2,
                                             bottom: -2,
                                             child: Container(
                                               width: 14,
                                               height: 14,
                                               decoration: BoxDecoration(
                                                 color: NetworkNode.getStatusColor(node.properties['status']?.toString()),
                                                 shape: BoxShape.circle,
                                                 border: Border.all(color: Colors.white, width: 2),
                                               ),
                                             ),
                                           ),
                                         ],
                                       ),
                                       const SizedBox(height: 4),
                                       RichText(
                                          textAlign: TextAlign.center,
                                          text: TextSpan(
                                             children: [
                                                TextSpan(
                                                   text: '${node.name}\n',
                                                   style: TextStyle(
                                                      color: node.isAnomaly ? Colors.red : Colors.black87,
                                                      fontSize: 11,
                                                      fontWeight: FontWeight.bold,
                                                   ),
                                                ),
                                                TextSpan(
                                                   text: '${node.properties['status'] ?? 'Unknown'}',
                                                   style: TextStyle(
                                                      color: node.isAnomaly ? Colors.red : Colors.black54,
                                                      fontSize: 9,
                                                      fontWeight: FontWeight.normal,
                                                   ),
                                                ),
                                             ],
                                          ),
                                       )
                                    ],
                                 ),
                              ),
                           );
                        }).toList(),
                      ],
                   ),
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
                    _buildLegendItem(Colors.grey, 'Unknown'),
                  ],
                ),
              ),
            ),
            // Floating restart physics button
            Positioned(
               bottom: 16,
               right: 16,
               child: FloatingActionButton(
                  mini: true,
                  backgroundColor: Colors.blueGrey,
                  onPressed: () {
                     setState(() {
                       _isSettled = false;
                     });
                     _controller.stop();
                     _initializePositions();
                     _precomputeLayout();
                  },
                  child: const Icon(Icons.refresh),
               )
            )
         ],
       ),
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
}

class TopologyPainter extends CustomPainter {
  final NetworkTopology topology;
  final Map<String, Offset> positions;

  TopologyPainter({
    required this.topology,
    required this.positions,
  });

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = Colors.blueGrey.withOpacity(0.5)
      ..strokeWidth = 2.0
      ..style = PaintingStyle.stroke;

    for (var conn in topology.connections) {
      final p1 = positions[conn.sourceId];
      final p2 = positions[conn.targetId];
      if (p1 != null && p2 != null) {
         canvas.drawLine(p1, p2, paint);
      }
    }
  }

  @override
  bool shouldRepaint(covariant TopologyPainter oldDelegate) {
    return oldDelegate.positions != positions;
  }
}
