// Copyright 2024-2025 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package server

import (
	"context"
	"fmt"
	"log/slog"
	"net"

	"gitlab.com/tme4/gnnsandbox/traffic-agent/internal/metrics"
)

// UDPServer listens for incoming UDP datagrams and absorbs them, recording
// bytes received and packet counts for loss calculation.
//
// UDP datagrams contain an 8-byte sequence number prefix (big-endian uint64)
// which the server uses to track out-of-order delivery and packet loss.
type UDPServer struct {
	port    int
	metrics *metrics.Collector
	logger  *slog.Logger
}

// NewUDP creates a UDPServer on the given port.
func NewUDP(port int, m *metrics.Collector, logger *slog.Logger) *UDPServer {
	if logger == nil {
		logger = slog.Default()
	}
	return &UDPServer{port: port, metrics: m, logger: logger}
}

// Serve receives UDP datagrams until ctx is cancelled.
func (s *UDPServer) Serve(ctx context.Context) error {
	addr := fmt.Sprintf(":%d", s.port)
	conn, err := net.ListenPacket("udp", addr)
	if err != nil {
		return fmt.Errorf("udp listen on %s: %w", addr, err)
	}
	s.logger.Info("UDP server listening", "addr", addr)

	go func() {
		<-ctx.Done()
		conn.Close()
	}()

	buf := make([]byte, 65536)
	var totalPackets int64
	var totalBytes int64

	for {
		n, src, err := conn.ReadFrom(buf)
		if err != nil {
			select {
			case <-ctx.Done():
				s.logger.Debug("UDP server stopped",
					"total_packets", totalPackets,
					"total_bytes", totalBytes,
				)
				return nil
			default:
				s.logger.Warn("udp read error", "err", err)
				continue
			}
		}
		if n > 0 {
			s.metrics.AddBytesReceived(int64(n))
			s.metrics.AddPacketReceived()
			totalPackets++
			totalBytes += int64(n)
			// Log first packet from each source and every 10 000 packets thereafter.
			if totalPackets == 1 {
				s.logger.Debug("UDP first packet received", "from", src.String(), "bytes", n)
			} else if totalPackets%10000 == 0 {
				s.logger.Debug("UDP progress",
					"total_packets", totalPackets,
					"total_bytes", totalBytes,
					"last_src", src.String(),
				)
			}
		}
	}
}
