import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../appstate.dart';
import '../models/vpn_info.dart';

// ── Phase → colour mapping ───────────────────────────────────────────────────

Color _phaseColor(String phase) {
  switch (phase.toLowerCase()) {
    case 'ready':
    case 'running':
    case 'operational':
      return Colors.green;
    case 'deploying':
    case 'pending':
    case 'processing':
    case 'waiting':
      return Colors.orange;
    case 'failed':
    case 'error':
      return Colors.red;
    case 'deleting':
      return Colors.deepOrange;
    default:
      return Colors.grey;
  }
}

// ── Shared badge widget ──────────────────────────────────────────────────────

class _PhaseBadge extends StatelessWidget {
  final String phase;
  const _PhaseBadge({required this.phase});

  @override
  Widget build(BuildContext context) {
    final color = _phaseColor(phase);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: color.withValues(alpha: 0.6)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 7,
            height: 7,
            decoration: BoxDecoration(color: color, shape: BoxShape.circle),
          ),
          const SizedBox(width: 5),
          Text(
            phase,
            style: TextStyle(
              color: color,
              fontSize: 11,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }
}

// ── Main panel widget ────────────────────────────────────────────────────────

/// Panel that shows VyOSL3VPN resources grouped with their related TrafficTests.
/// Each VPN card contains:
///   • VPN header (name, phase, routers, underlay)
///   • Collapsible "Route Info" sub-section with per-router VRF RD/RT table
///   • Collapsible "Traffic Tests" sub-section with related test cards
///
/// Traffic tests not linked to any VPN appear at the bottom in a separate section.
class VpnTrafficPanel extends StatefulWidget {
  const VpnTrafficPanel({super.key});

  @override
  State<VpnTrafficPanel> createState() => _VpnTrafficPanelState();
}

class _VpnTrafficPanelState extends State<VpnTrafficPanel> {
  String? _selectedVpnName;
  String? _selectedTestName;

  /// VPN names whose "Route Info" sub-section is expanded.
  final Set<String> _expandedRouteInfo = {};

  /// VPN names whose "Traffic Tests" sub-section is expanded (open by default).
  final Set<String> _expandedTests = {};

  /// Tracks VPN names we have already seeded into _expandedTests so we don't
  /// force them back open on subsequent rebuilds.
  final Set<String> _seenVpns = {};

  /// Per-VPN horizontal scroll controllers for the Route Info table.
  final Map<String, ScrollController> _routeScrollControllers = {};

  ScrollController _routeScrollController(String vpnName) =>
      _routeScrollControllers.putIfAbsent(
        vpnName,
        () => ScrollController(),
      );

  @override
  void dispose() {
    for (final c in _routeScrollControllers.values) {
      c.dispose();
    }
    super.dispose();
  }

  // ── Text styles ─────────────────────────────────────────────────────────

  static const _sectionHeaderStyle = TextStyle(
    fontWeight: FontWeight.bold,
    fontSize: 12,
    color: Color(0xFF0D47A1),
  );

  static const _labelStyle = TextStyle(fontSize: 10, color: Colors.black54);
  static const _valueStyle = TextStyle(fontSize: 11, color: Colors.black87);
  static const _nameStyle = TextStyle(
    fontWeight: FontWeight.w600,
    fontSize: 12,
    color: Colors.black87,
  );

  // ── Build ────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    return Consumer<Appstate>(
      builder: (context, appState, _) {
        // Auto-expand tests section only the first time a VPN is seen.
        for (final vpn in appState.vpns) {
          if (_seenVpns.add(vpn.name)) {
            _expandedTests.add(vpn.name);
          }
        }

        final linkedTests = <String, List<TrafficTestInfo>>{};
        final unlinkedTests = <TrafficTestInfo>[];

        for (final test in appState.trafficTests) {
          if (test.vpnRef != null && test.vpnRef!.isNotEmpty) {
            linkedTests.putIfAbsent(test.vpnRef!, () => []).add(test);
          } else {
            unlinkedTests.add(test);
          }
        }

        final deletingVpn = appState.deletingVpnName;

        return Container(
          color: const Color(0xFFF5F7FA),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              _buildPanelHeader(appState),
              // VPN delete progress banner
              if (deletingVpn != null) _buildVpnDeletingBanner(deletingVpn),
              Expanded(
                child: ListView(
                  padding: const EdgeInsets.only(bottom: 16),
                  children: [
                    if (appState.vpns.isEmpty && appState.trafficTests.isEmpty)
                      _buildEmptyState()
                    else ...[
                      // One card per VPN
                      for (final vpn in appState.vpns)
                        _buildVpnCard(
                          vpn,
                          linkedTests[vpn.name] ?? [],
                          appState,
                        ),
                      // Unlinked tests section
                      if (unlinkedTests.isNotEmpty)
                        _buildUnlinkedTestsSection(unlinkedTests, appState),
                    ],
                  ],
                ),
              ),
            ],
          ),
        );
      },
    );
  }

  // ── VPN delete progress banner ────────────────────────────────────────────

  Widget _buildVpnDeletingBanner(String vpnName) {
    return Container(
      color: Colors.deepOrange.shade50,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      child: Row(
        children: [
          const SizedBox(
            width: 14,
            height: 14,
            child: CircularProgressIndicator(
              strokeWidth: 2,
              color: Colors.deepOrange,
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              'Deleting VPN "$vpnName" and linked tests…',
              style: TextStyle(
                fontSize: 11,
                color: Colors.deepOrange.shade800,
                fontWeight: FontWeight.w500,
              ),
              overflow: TextOverflow.ellipsis,
            ),
          ),
        ],
      ),
    );
  }

  // ── Panel title bar ──────────────────────────────────────────────────────

  Widget _buildPanelHeader(Appstate appState) {
    final hasHighlight = appState.highlightedNodeIds.isNotEmpty;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      color: const Color(0xFF0D47A1),
      child: Row(
        children: [
          const Icon(Icons.hub_outlined, color: Colors.white, size: 18),
          const SizedBox(width: 8),
          const Expanded(
            child: Text(
              'VPNs & Traffic Tests',
              style: TextStyle(
                color: Colors.white,
                fontWeight: FontWeight.bold,
                fontSize: 13,
              ),
            ),
          ),
          if (hasHighlight)
            Tooltip(
              message: 'Clear topology highlight',
              child: IconButton(
                icon: const Icon(Icons.highlight_off,
                    color: Colors.white70, size: 18),
                padding: EdgeInsets.zero,
                constraints: const BoxConstraints(),
                onPressed: () {
                  setState(() {
                    _selectedVpnName = null;
                    _selectedTestName = null;
                  });
                  appState.clearHighlight();
                },
              ),
            ),
          Tooltip(
            message: 'Refresh',
            child: IconButton(
              icon: const Icon(Icons.refresh, color: Colors.white70, size: 18),
              padding: EdgeInsets.zero,
              constraints: const BoxConstraints(),
              onPressed: () => appState.refreshVpnsAndTests(),
            ),
          ),
        ],
      ),
    );
  }

  // ── VPN card ─────────────────────────────────────────────────────────────

  Widget _buildVpnCard(
    VpnInfo vpn,
    List<TrafficTestInfo> relatedTests,
    Appstate appState,
  ) {
    final isSelected = _selectedVpnName == vpn.name;
    final routeExpanded = _expandedRouteInfo.contains(vpn.name);
    final testsExpanded = _expandedTests.contains(vpn.name);

    return AnimatedContainer(
      duration: const Duration(milliseconds: 150),
      margin: const EdgeInsets.fromLTRB(8, 8, 8, 0),
      decoration: BoxDecoration(
        color: isSelected ? Colors.blue.shade50 : Colors.white,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(
          color: isSelected ? Colors.blue.shade300 : Colors.grey.shade200,
          width: isSelected ? 1.5 : 1,
        ),
        boxShadow: [
          BoxShadow(
            color: isSelected
                ? Colors.blue.withValues(alpha: 0.10)
                : Colors.black.withValues(alpha: 0.04),
            blurRadius: 4,
            offset: const Offset(0, 2),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // ── VPN header (selectable) ──────────────────────────────────────
          InkWell(
            borderRadius: const BorderRadius.vertical(top: Radius.circular(10)),
            onTap: () {
              setState(() {
                if (isSelected) {
                  _selectedVpnName = null;
                  appState.clearHighlight();
                } else {
                  _selectedVpnName = vpn.name;
                  _selectedTestName = null;
                  appState.highlightVpnNodes(vpn);
                }
              });
            },
            child: Padding(
              padding: const EdgeInsets.all(10),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  // Name + phase + delete button
                  Row(
                    children: [
                      const Icon(Icons.vpn_lock,
                          size: 14, color: Color(0xFF0D47A1)),
                      const SizedBox(width: 5),
                      Expanded(
                        child: Text(
                          vpn.name,
                          style: _nameStyle,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                      _PhaseBadge(phase: vpn.phase),
                      const SizedBox(width: 4),
                      // VPN delete button — disabled while any delete is in progress
                      _buildVpnDeleteButton(vpn, appState),
                    ],
                  ),
                  // Underlay ref
                  if (vpn.underlayRef != null) ...[
                    const SizedBox(height: 3),
                    Row(
                      children: [
                        const SizedBox(width: 19),
                        Text('Underlay: ', style: _labelStyle),
                        Expanded(
                          child: Text(
                            vpn.underlayRef!,
                            style: const TextStyle(
                                fontSize: 10, color: Colors.black54),
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                      ],
                    ),
                  ],
                  // Router chips
                  if (vpn.routers.isNotEmpty) ...[
                    const SizedBox(height: 6),
                    Wrap(
                      spacing: 4,
                      runSpacing: 2,
                      children: vpn.routers
                          .map(
                            (r) => _RouterChip(name: r),
                          )
                          .toList(),
                    ),
                  ],
                  // Status message
                  if (vpn.message.isNotEmpty) ...[
                    const SizedBox(height: 4),
                    Text(
                      vpn.message,
                      style: _labelStyle,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ],
                ],
              ),
            ),
          ),

          // ── Route Info sub-section ───────────────────────────────────────
          if (vpn.hasRouteInfo) ...[
            const Divider(height: 1, thickness: 1, indent: 10, endIndent: 10),
            _buildSubSectionToggle(
              label: 'Route Info',
              icon: Icons.alt_route,
              expanded: routeExpanded,
              onToggle: () => setState(() {
                if (routeExpanded) {
                  _expandedRouteInfo.remove(vpn.name);
                } else {
                  _expandedRouteInfo.add(vpn.name);
                }
              }),
            ),
            if (routeExpanded) _buildRouteInfoTable(vpn),
          ],

          // ── Traffic Tests sub-section ─────────────────────────────────────
          const Divider(height: 1, thickness: 1, indent: 10, endIndent: 10),
          _buildSubSectionToggle(
            label: 'Traffic Tests',
            icon: Icons.speed,
            count: relatedTests.length,
            expanded: testsExpanded,
            onToggle: () => setState(() {
              if (testsExpanded) {
                _expandedTests.remove(vpn.name);
              } else {
                _expandedTests.add(vpn.name);
              }
            }),
          ),
          if (testsExpanded) ...[
            if (relatedTests.isEmpty)
              Padding(
                padding:
                    const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
                child: Text(
                  'No traffic tests linked to this VPN',
                  style: _labelStyle.copyWith(fontStyle: FontStyle.italic),
                ),
              )
            else
              for (final test in relatedTests)
                _buildTestCard(test, appState, indent: true),
            const SizedBox(height: 4),
          ],
        ],
      ),
    );
  }

  // ── Sub-section toggle row ────────────────────────────────────────────────

  Widget _buildSubSectionToggle({
    required String label,
    required IconData icon,
    required bool expanded,
    required VoidCallback onToggle,
    int? count,
  }) {
    return InkWell(
      onTap: onToggle,
      child: Padding(
        padding:
            const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        child: Row(
          children: [
            Icon(icon, size: 13, color: const Color(0xFF0D47A1)),
            const SizedBox(width: 5),
            Text(label, style: _sectionHeaderStyle),
            if (count != null) ...[
              const SizedBox(width: 5),
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
                decoration: BoxDecoration(
                  color: const Color(0xFF0D47A1).withValues(alpha: 0.1),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Text(
                  '$count',
                  style: const TextStyle(
                    fontSize: 10,
                    fontWeight: FontWeight.bold,
                    color: Color(0xFF0D47A1),
                  ),
                ),
              ),
            ],
            const Spacer(),
            Icon(
              expanded ? Icons.expand_less : Icons.expand_more,
              size: 16,
              color: Colors.black38,
            ),
          ],
        ),
      ),
    );
  }

  // ── Route info table ──────────────────────────────────────────────────────

  Widget _buildRouteInfoTable(VpnInfo vpn) {
    const headerStyle = TextStyle(
      fontSize: 10,
      fontWeight: FontWeight.bold,
      color: Colors.black54,
    );
    const cellStyle = TextStyle(fontSize: 10, color: Colors.black87);
    const monoStyle = TextStyle(
      fontSize: 10,
      color: Color(0xFF1A237E),
      fontFamily: 'monospace',
    );

    final scrollCtrl = _routeScrollController(vpn.name);

    return Padding(
      padding: const EdgeInsets.fromLTRB(10, 0, 10, 8),
      child: Container(
        decoration: BoxDecoration(
          color: const Color(0xFFF0F4FF),
          borderRadius: BorderRadius.circular(6),
          border: Border.all(color: Colors.blue.shade100),
        ),
        child: Scrollbar(
          controller: scrollCtrl,
          thumbVisibility: true,
          child: SingleChildScrollView(
            controller: scrollCtrl,
            scrollDirection: Axis.horizontal,
            child: DataTable(
            headingRowHeight: 28,
            dataRowMinHeight: 26,
            dataRowMaxHeight: 32,
            horizontalMargin: 10,
            columnSpacing: 12,
            headingRowColor: WidgetStateProperty.all(Colors.blue.shade50),
            columns: const [
              DataColumn(label: Text('Router', style: headerStyle)),
              DataColumn(label: Text('VRF', style: headerStyle)),
              DataColumn(label: Text('RD', style: headerStyle)),
              DataColumn(label: Text('RT Export', style: headerStyle)),
              DataColumn(label: Text('RT Import', style: headerStyle)),
            ],
            rows: vpn.routerVrfs.map((vrf) {
              String joinRts(List<String> v) =>
                  v.isEmpty ? '—' : v.join(', ');
              return DataRow(cells: [
                DataCell(Text(vrf.router, style: cellStyle)),
                DataCell(Text(vrf.vrf, style: cellStyle)),
                DataCell(Text(vrf.rd ?? '—', style: monoStyle)),
                DataCell(Text(joinRts(vrf.rtExport), style: monoStyle)),
                DataCell(Text(joinRts(vrf.rtImport), style: monoStyle)),
              ]);
            }).toList(),
            ),
          ),
        ),
      ),
    );
  }

  // ── Traffic test card ─────────────────────────────────────────────────────

  Widget _buildTestCard(
    TrafficTestInfo test,
    Appstate appState, {
    bool indent = false,
  }) {
    final isSelected = _selectedTestName == test.name;

    return Padding(
      padding: EdgeInsets.fromLTRB(indent ? 14 : 8, 3, indent ? 14 : 8, 3),
      child: InkWell(
        borderRadius: BorderRadius.circular(8),
        onTap: () {
          setState(() {
            if (isSelected) {
              _selectedTestName = null;
              appState.clearHighlight();
            } else {
              _selectedTestName = test.name;
              _selectedVpnName = null;
              appState.highlightTrafficTestNodes(test);
            }
          });
        },
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 150),
          padding: const EdgeInsets.all(9),
          decoration: BoxDecoration(
            color: isSelected ? Colors.teal.shade50 : Colors.grey.shade50,
            borderRadius: BorderRadius.circular(8),
            border: Border.all(
              color:
                  isSelected ? Colors.teal.shade300 : Colors.grey.shade200,
              width: isSelected ? 1.5 : 1,
            ),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Name + phase + delete button
              Row(
                children: [
                  const Icon(Icons.speed, size: 12, color: Colors.teal),
                  const SizedBox(width: 4),
                  Expanded(
                    child: Text(
                      test.name,
                      style: const TextStyle(
                        fontWeight: FontWeight.w600,
                        fontSize: 11,
                        color: Colors.black87,
                      ),
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  _PhaseBadge(phase: test.phase),
                  const SizedBox(width: 2),
                  _buildTestDeleteButton(test, appState),
                ],
              ),

              const SizedBox(height: 4),

              // Traffic params line: Protocol • Bandwidth • Pattern • Ns [↔]
              _buildTrafficParamsLine(test),

              const SizedBox(height: 4),

              // Source(s) → Destination
              _buildFlowLine(test),

              // Timing
              if (test.startTime != null) ...[
                const SizedBox(height: 3),
                Row(
                  children: [
                    const Icon(Icons.schedule,
                        size: 10, color: Colors.black38),
                    const SizedBox(width: 3),
                    Text(
                      'Started: ${_formatTime(test.startTime!)}',
                      style: _labelStyle,
                    ),
                    if (test.endTime != null) ...[
                      const Text('  →  ', style: TextStyle(fontSize: 10)),
                      Text(_formatTime(test.endTime!), style: _labelStyle),
                    ],
                  ],
                ),
              ],

              // Status message
              if (test.message.isNotEmpty) ...[
                const SizedBox(height: 3),
                Text(
                  test.message,
                  style: _labelStyle,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }

  /// One-line compact summary: Protocol • Bandwidth • Pattern • Ns [↔]
  Widget _buildTrafficParamsLine(TrafficTestInfo test) {
    final parts = <String>[];
    if (test.protocol != null) parts.add(test.protocol!);
    if (test.bandwidth != null) parts.add(test.bandwidth!);
    if (test.patternType != null) parts.add(test.patternType!);
    parts.add('${test.duration}s');

    return Row(
      children: [
        Expanded(
          child: Text(
            parts.join(' • '),
            style: const TextStyle(
              fontSize: 10,
              color: Color(0xFF00695C),
              fontWeight: FontWeight.w500,
            ),
            overflow: TextOverflow.ellipsis,
          ),
        ),
        if (test.bidirectional)
          const Tooltip(
            message: 'Bidirectional',
            child: Icon(Icons.swap_horiz, size: 14, color: Colors.teal),
          ),
      ],
    );
  }

  /// Source device(s) → Destination line.
  Widget _buildFlowLine(TrafficTestInfo test) {
    final srcs = test.sourceDevices;
    final dest = test.destinationDevice;
    if (srcs.isEmpty && dest == null) return const SizedBox.shrink();

    final srcText = srcs.isEmpty
        ? '?'
        : srcs.length == 1
            ? srcs.first
            : '${srcs.first} +${srcs.length - 1}';

    return Row(
      children: [
        const Icon(Icons.device_hub, size: 10, color: Colors.black38),
        const SizedBox(width: 3),
        Flexible(
          child: Text(
            srcText,
            style: _valueStyle.copyWith(fontSize: 10),
            overflow: TextOverflow.ellipsis,
          ),
        ),
        const Padding(
          padding: EdgeInsets.symmetric(horizontal: 4),
          child: Icon(Icons.arrow_forward, size: 10, color: Colors.black38),
        ),
        Flexible(
          child: Text(
            dest ?? '?',
            style: _valueStyle.copyWith(fontSize: 10),
            overflow: TextOverflow.ellipsis,
          ),
        ),
        // Show all sources tooltip if there are multiple
        if (srcs.length > 1)
          Tooltip(
            message: srcs.join('\n'),
            child: const Icon(Icons.info_outline,
                size: 12, color: Colors.black38),
          ),
      ],
    );
  }

  // ── Unlinked tests section ────────────────────────────────────────────────

  Widget _buildUnlinkedTestsSection(
    List<TrafficTestInfo> tests,
    Appstate appState,
  ) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // Section header
        Padding(
          padding: const EdgeInsets.fromLTRB(8, 10, 8, 4),
          child: Row(
            children: [
              const Icon(Icons.link_off, size: 13, color: Colors.black45),
              const SizedBox(width: 5),
              const Text(
                'Unlinked Traffic Tests',
                style: TextStyle(
                  fontWeight: FontWeight.bold,
                  fontSize: 12,
                  color: Colors.black54,
                ),
              ),
              const SizedBox(width: 5),
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
                decoration: BoxDecoration(
                  color: Colors.black12,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Text(
                  '${tests.length}',
                  style: const TextStyle(
                    fontSize: 10,
                    fontWeight: FontWeight.bold,
                    color: Colors.black54,
                  ),
                ),
              ),
            ],
          ),
        ),
        for (final test in tests) _buildTestCard(test, appState),
      ],
    );
  }

  // ── Delete buttons ────────────────────────────────────────────────────────

  /// Small trash-icon button for a VPN card.  Disabled while any VPN delete
  /// is in progress.
  Widget _buildVpnDeleteButton(VpnInfo vpn, Appstate appState) {
    final isDeleting = appState.deletingVpnName != null;
    return Tooltip(
      message: isDeleting ? 'Delete in progress…' : 'Delete VPN and linked tests',
      child: InkWell(
        borderRadius: BorderRadius.circular(4),
        onTap: isDeleting
            ? null
            : () => _confirmDeleteVpn(
                  vpn,
                  appState.trafficTests
                      .where((t) => t.vpnRef == vpn.name)
                      .length,
                  appState,
                ),
        child: Padding(
          padding: const EdgeInsets.all(4),
          child: Icon(
            Icons.delete_outline,
            size: 15,
            color: isDeleting ? Colors.black26 : Colors.red.shade400,
          ),
        ),
      ),
    );
  }

  /// Small trash-icon button for a test card.
  /// Shows a spinner and is disabled while the test's delete is in flight.
  Widget _buildTestDeleteButton(TrafficTestInfo test, Appstate appState) {
    final isDeleting = appState.deletingTestNames.contains(test.name);
    if (isDeleting) {
      return const Padding(
        padding: EdgeInsets.all(4),
        child: SizedBox(
          width: 13,
          height: 13,
          child: CircularProgressIndicator(
            strokeWidth: 1.5,
            color: Colors.red,
          ),
        ),
      );
    }
    return Tooltip(
      message: 'Delete traffic test',
      child: InkWell(
        borderRadius: BorderRadius.circular(4),
        onTap: () => _confirmDeleteTest(test, appState),
        child: Padding(
          padding: const EdgeInsets.all(4),
          child: Icon(
            Icons.delete_outline,
            size: 13,
            color: Colors.red.shade300,
          ),
        ),
      ),
    );
  }

  // ── Confirmation dialogs ──────────────────────────────────────────────────

  Future<void> _confirmDeleteVpn(
    VpnInfo vpn,
    int linkedTestCount,
    Appstate appState,
  ) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Row(
          children: [
            Icon(Icons.warning_amber_rounded, color: Colors.deepOrange),
            SizedBox(width: 8),
            Text('Delete VPN', style: TextStyle(fontSize: 16)),
          ],
        ),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            RichText(
              text: TextSpan(
                style: const TextStyle(fontSize: 13, color: Colors.black87),
                children: [
                  const TextSpan(text: 'Delete VPN '),
                  TextSpan(
                    text: vpn.name,
                    style: const TextStyle(fontWeight: FontWeight.bold),
                  ),
                  const TextSpan(text: '?'),
                ],
              ),
            ),
            if (linkedTestCount > 0) ...[
              const SizedBox(height: 8),
              Text(
                '$linkedTestCount linked traffic test${linkedTestCount == 1 ? '' : 's'} will also be deleted.',
                style: const TextStyle(
                    fontSize: 12, color: Colors.deepOrange),
              ),
            ],
            const SizedBox(height: 8),
            const Text(
              'This action cannot be undone.',
              style: TextStyle(fontSize: 11, color: Colors.black54),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            style: FilledButton.styleFrom(
                backgroundColor: Colors.deepOrange),
            onPressed: () => Navigator.of(ctx).pop(true),
            child: const Text('Delete'),
          ),
        ],
      ),
    );

    if (confirmed == true && mounted) {
      final accepted = await appState.deleteVpn(vpn.name);
      if (!accepted && mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('A VPN delete is already in progress. Please wait.'),
            backgroundColor: Colors.deepOrange,
          ),
        );
      }
    }
  }

  Future<void> _confirmDeleteTest(
    TrafficTestInfo test,
    Appstate appState,
  ) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Row(
          children: [
            Icon(Icons.warning_amber_rounded, color: Colors.orange),
            SizedBox(width: 8),
            Text('Delete Test', style: TextStyle(fontSize: 16)),
          ],
        ),
        content: RichText(
          text: TextSpan(
            style: const TextStyle(fontSize: 13, color: Colors.black87),
            children: [
              const TextSpan(text: 'Delete traffic test '),
              TextSpan(
                text: test.name,
                style: const TextStyle(fontWeight: FontWeight.bold),
              ),
              const TextSpan(text: '?'),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: Colors.red),
            onPressed: () => Navigator.of(ctx).pop(true),
            child: const Text('Delete'),
          ),
        ],
      ),
    );

    if (confirmed == true && mounted) {
      await appState.deleteTrafficTest(test.name);
    }
  }

  // ── Empty state ───────────────────────────────────────────────────────────

  Widget _buildEmptyState() {
    return const Padding(
      padding: EdgeInsets.symmetric(horizontal: 16, vertical: 24),
      child: Column(
        children: [
          Icon(Icons.hub_outlined, size: 32, color: Colors.black26),
          SizedBox(height: 8),
          Text(
            'No VPNs or traffic tests provisioned',
            style: TextStyle(color: Colors.black38, fontSize: 12),
            textAlign: TextAlign.center,
          ),
        ],
      ),
    );
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  String _formatTime(String isoTime) {
    try {
      final dt = DateTime.parse(isoTime).toLocal();
      final h = dt.hour.toString().padLeft(2, '0');
      final m = dt.minute.toString().padLeft(2, '0');
      return '${dt.year}-${dt.month.toString().padLeft(2, '0')}-'
          '${dt.day.toString().padLeft(2, '0')} $h:$m';
    } catch (_) {
      return isoTime;
    }
  }
}

// ── Router chip ──────────────────────────────────────────────────────────────

class _RouterChip extends StatelessWidget {
  final String name;
  const _RouterChip({required this.name});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
      decoration: BoxDecoration(
        color: Colors.blue.shade50,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: Colors.blue.shade200),
      ),
      child: Text(
        name,
        style: TextStyle(fontSize: 10, color: Colors.blue.shade700),
      ),
    );
  }
}
