// Copyright 2024-2025 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

import 'package:flutter/material.dart';

/// Card that displays underlay (Layer 2) routing-protocol metrics for a P/PE
/// router node.
///
/// The [data] map is the JSON response from  GET /metrics/routing/{node_id}
/// and contains sections for OSPF, BGP peers, routing table, FRR collector
/// health, and BFD.
class UnderlayMetricsCard extends StatelessWidget {
  final Map<String, dynamic> data;

  const UnderlayMetricsCard({super.key, required this.data});

  // ── colour palette ──────────────────────────────────────────────────────

  static const Color _headerColor  = Color(0xFF1565C0); // dark blue
  static const Color _okColor      = Color(0xFF2E7D32); // green
  static const Color _warnColor    = Color(0xFFE65100); // orange
  static const Color _errorColor   = Color(0xFFC62828); // red
  static const Color _greyColor    = Colors.black45;

  // ── text styles ─────────────────────────────────────────────────────────

  static const _titleStyle = TextStyle(
    fontSize: 14,
    fontWeight: FontWeight.bold,
    color: _headerColor,
  );
  static const _sectionStyle = TextStyle(
    fontSize: 11,
    fontWeight: FontWeight.bold,
    color: _headerColor,
  );
  static const _labelStyle = TextStyle(fontSize: 10, color: Colors.black54);
  static const _valueStyle = TextStyle(
    fontSize: 11,
    fontWeight: FontWeight.w600,
    color: Colors.black87,
  );

  // ── helpers ─────────────────────────────────────────────────────────────

  /// Format BGP uptime seconds → "Xd Xh Xm" (or "down" if 0 / null).
  String _formatUptime(double? seconds) {
    if (seconds == null) return '—';
    if (seconds <= 0) return 'down';
    final total = seconds.toInt();
    final d = total ~/ 86400;
    final h = (total % 86400) ~/ 3600;
    final m = (total % 3600) ~/ 60;
    if (d > 0) return '${d}d ${h}h';
    if (h > 0) return '${h}h ${m}m';
    return '${m}m';
  }

  Color _uptimeColor(double? seconds) {
    if (seconds == null) return _greyColor;
    if (seconds <= 0) return _errorColor;
    return _okColor;
  }

  Color _routeColor(int? total, int? fib) {
    if (total == null || fib == null) return _greyColor;
    if (total == 0) return _greyColor;
    if (fib < total) return _warnColor;
    return _okColor;
  }

  Color _collectorColor(int val) => val == 1 ? _okColor : _errorColor;

  // ── build ────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    final ospf       = data['ospf']       as Map<String, dynamic>? ?? {};
    final bgpPeers   = (data['bgp_peers'] as List?)?.cast<Map<String, dynamic>>() ?? [];
    final routes     = (data['routes']    as List?)?.cast<Map<String, dynamic>>() ?? [];
    final collectors = (data['collectors'] as Map<String, dynamic>?) ?? {};
    final bfdPeers   = data['bfd_peers']  as int?;
    final timestamp  = data['timestamp']  as String?;

    // Skip rendering entirely if there is no data (e.g. device node).
    final hasData = ospf.isNotEmpty ||
        bgpPeers.isNotEmpty ||
        routes.isNotEmpty ||
        collectors.isNotEmpty ||
        bfdPeers != null;

    if (!hasData) return const SizedBox.shrink();

    return Card(
      elevation: 2,
      margin: const EdgeInsets.all(4),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(8),
        side: const BorderSide(color: Color(0xFF1565C0), width: 1),
      ),
      child: Padding(
        padding: const EdgeInsets.all(10),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            // ── Header ──────────────────────────────────────────────────
            Row(
              children: [
                const Icon(Icons.account_tree_outlined,
                    color: _headerColor, size: 16),
                const SizedBox(width: 6),
                const Text('Underlay Network (Layer 2)', style: _titleStyle),
                const Spacer(),
                if (timestamp != null)
                  Text(
                    _fmtTimestamp(timestamp),
                    style: _labelStyle.copyWith(fontSize: 9),
                  ),
              ],
            ),
            const Divider(height: 12),

            // ── OSPF ────────────────────────────────────────────────────
            if (ospf.isNotEmpty) ...[
              _sectionHeader('OSPF', Icons.hub_outlined),
              const SizedBox(height: 4),
              Row(
                children: [
                  _statCell('Neighbors',
                      '${ospf['neighbors'] ?? 0}',
                      (ospf['neighbors'] as int? ?? 0) > 0
                          ? _okColor
                          : _warnColor),
                  const SizedBox(width: 16),
                  _statCell('Adjacencies',
                      '${ospf['adjacencies'] ?? 0}',
                      (ospf['adjacencies'] as int? ?? 0) > 0
                          ? _okColor
                          : _errorColor),
                ],
              ),
              const SizedBox(height: 8),
            ],

            // ── BGP Peers ────────────────────────────────────────────────
            if (bgpPeers.isNotEmpty) ...[
              _sectionHeader('iBGP Peers', Icons.swap_horiz),
              const SizedBox(height: 4),
              for (final peer in bgpPeers) _buildBgpRow(peer),
              const SizedBox(height: 8),
            ],

            // ── Routing Table ───────────────────────────────────────────
            if (routes.isNotEmpty) ...[
              _sectionHeader('Routing Table', Icons.route),
              const SizedBox(height: 4),
              for (final r in routes) _buildRouteRow(r),
              const SizedBox(height: 8),
            ],

            // ── BFD ──────────────────────────────────────────────────────
            if (bfdPeers != null) ...[
              Row(
                children: [
                  _sectionHeader('BFD', Icons.timer_outlined),
                  const SizedBox(width: 8),
                  Text(
                    '$bfdPeers peer${bfdPeers == 1 ? '' : 's'}',
                    style: _valueStyle.copyWith(
                      color: bfdPeers > 0 ? _okColor : _greyColor,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 8),
            ],

            // ── FRR Collector Health ─────────────────────────────────────
            if (collectors.isNotEmpty) ...[
              _sectionHeader('FRR Collectors', Icons.monitor_heart_outlined),
              const SizedBox(height: 4),
              Wrap(
                spacing: 8,
                runSpacing: 4,
                children: collectors.entries.map((e) {
                  final up = (e.value as num?)?.toInt() ?? 0;
                  final color = _collectorColor(up);
                  return Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(
                        up == 1
                            ? Icons.circle
                            : Icons.circle_outlined,
                        size: 8,
                        color: color,
                      ),
                      const SizedBox(width: 3),
                      Text(e.key, style: _labelStyle.copyWith(color: color)),
                    ],
                  );
                }).toList(),
              ),
            ],
          ],
        ),
      ),
    );
  }

  // ── Helper builders ──────────────────────────────────────────────────────

  Widget _sectionHeader(String label, IconData icon) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, size: 12, color: _headerColor),
        const SizedBox(width: 4),
        Text(label, style: _sectionStyle),
      ],
    );
  }

  Widget _statCell(String label, String value, Color valueColor) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(value,
            style: _valueStyle.copyWith(fontSize: 13, color: valueColor)),
        Text(label, style: _labelStyle),
      ],
    );
  }

  Widget _buildBgpRow(Map<String, dynamic> peer) {
    final neighbor = peer['neighbor'] as String? ?? '?';
    final afi      = peer['afi']      as String? ?? '';
    final vrf      = peer['vrf']      as String? ?? 'default';
    final uptime   = (peer['uptime_seconds'] as num?)?.toDouble();

    final label = vrf == 'default' ? afi : '$afi/$vrf';

    return Padding(
      padding: const EdgeInsets.only(bottom: 3),
      child: Row(
        children: [
          const Icon(Icons.chevron_right, size: 12, color: Colors.black38),
          const SizedBox(width: 2),
          Expanded(
            flex: 3,
            child: Text(
              neighbor,
              style: const TextStyle(fontSize: 10, color: Colors.black87,
                  fontFamily: 'monospace'),
              overflow: TextOverflow.ellipsis,
            ),
          ),
          if (label.isNotEmpty) ...[
            const SizedBox(width: 4),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
              decoration: BoxDecoration(
                color: Colors.blue.shade50,
                borderRadius: BorderRadius.circular(4),
              ),
              child: Text(label,
                  style: const TextStyle(fontSize: 9, color: Color(0xFF1565C0))),
            ),
          ],
          const SizedBox(width: 8),
          Text(
            _formatUptime(uptime),
            style: TextStyle(
              fontSize: 10,
              fontWeight: FontWeight.w600,
              color: _uptimeColor(uptime),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildRouteRow(Map<String, dynamic> r) {
    final afi   = r['afi']   as String? ?? '';
    final vrf   = r['vrf']   as String? ?? 'default';
    final total = r['total'] as int?;
    final fib   = r['fib']   as int?;
    final color = _routeColor(total, fib);

    final label = vrf == 'default' ? afi : '$afi/$vrf';

    return Padding(
      padding: const EdgeInsets.only(bottom: 3),
      child: Row(
        children: [
          const Icon(Icons.chevron_right, size: 12, color: Colors.black38),
          const SizedBox(width: 2),
          Text(label,
              style: const TextStyle(fontSize: 10, color: Colors.black87)),
          const Spacer(),
          Text(
            total != null ? '$total total' : '—',
            style: const TextStyle(fontSize: 10, color: Colors.black54),
          ),
          const SizedBox(width: 8),
          Text(
            fib != null ? '$fib FIB' : '—',
            style: TextStyle(
              fontSize: 10,
              fontWeight: FontWeight.w600,
              color: color,
            ),
          ),
        ],
      ),
    );
  }

  String _fmtTimestamp(String iso) {
    try {
      final dt = DateTime.parse(iso).toLocal();
      final h  = dt.hour.toString().padLeft(2, '0');
      final m  = dt.minute.toString().padLeft(2, '0');
      return '${dt.day}/${dt.month} $h:$m';
    } catch (_) {
      return iso;
    }
  }
}
