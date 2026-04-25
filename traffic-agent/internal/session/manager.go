// Copyright 2024-2025 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Package session manages pools of concurrent traffic-sending goroutines.
// Each goroutine owns one TCP or UDP connection and paces its output via a
// shared rate limiter that the PatternController updates every 100 ms.
package session

import (
	"context"
	"fmt"
	"log/slog"
	"math/rand"
	"net"
	"sync"
	"sync/atomic"
	"time"

	"gitlab.com/tme4/gnnsandbox/traffic-agent/internal/metrics"
	"gitlab.com/tme4/gnnsandbox/traffic-agent/internal/patterns"
	"gitlab.com/tme4/gnnsandbox/traffic-agent/internal/ratelimit"
)

// Manager runs a fixed pool of concurrent sessions against a single
// (destIP:port, protocol) target.  All sessions share one rate limiter whose
// fill rate is driven by the pattern's BandwidthAt function.
type Manager struct {
	destAddr string
	protocol string // "tcp" or "udp"
	sessions int
	pattern  patterns.Pattern
	metrics  *metrics.Collector
	logger   *slog.Logger

	// activeSessions is the live count read by the pattern controller.
	activeSessions atomic.Int32
}

// New creates a Manager.  Call Run to start traffic.
func New(
	destIP string,
	port int,
	protocol string,
	numSessions int,
	pat patterns.Pattern,
	m *metrics.Collector,
	logger *slog.Logger,
) *Manager {
	if logger == nil {
		logger = slog.Default()
	}
	return &Manager{
		destAddr: fmt.Sprintf("%s:%d", destIP, port),
		protocol: protocol,
		sessions: numSessions,
		pattern:  pat,
		metrics:  m,
		logger:   logger,
	}
}

// Run starts numSessions goroutines and blocks until ctx is cancelled or
// duration elapses.  Each goroutine independently connects and sends data.
func (m *Manager) Run(ctx context.Context, duration time.Duration) error {
	startTime := time.Now()
	m.pattern.SetStartTime(startTime)

	// Create a shared rate limiter — all sessions divide the total budget.
	// The PatternController updates it every 100 ms based on BandwidthAt.
	initialBps := m.pattern.BandwidthAt(startTime)
	perSessionBps := initialBps
	if m.sessions > 1 {
		perSessionBps = initialBps / int64(m.sessions)
	}
	lim := ratelimit.New(perSessionBps)

	// Start the pattern controller that updates lim on each tick.
	pc := ratelimit.NewPatternController(lim, 100*time.Millisecond, m.logger)
	ctxWithTimeout, cancel := context.WithTimeout(ctx, duration)
	defer cancel()

	go pc.Run(ctxWithTimeout, func(t time.Time) int64 {
		total := m.pattern.BandwidthAt(t)
		active := m.activeSessions.Load()
		if active < 1 {
			active = 1
		}
		return total / int64(active)
	})

	// Periodic stats logger: log throughput every 10 s so progress is visible
	// in container logs without needing LOG_LEVEL=debug.
	go m.logStats(ctxWithTimeout, startTime)

	// Check if this is a Poisson pattern — those self-manage arrivals.
	if pp, ok := m.pattern.(*patterns.PoissonPattern); ok {
		return m.runPoisson(ctxWithTimeout, pp, lim)
	}

	// Standard pool: launch all sessions immediately.
	return m.runPool(ctxWithTimeout, lim)
}

// logStats emits periodic throughput/session metrics to the structured log.
// It runs for the lifetime of ctxWithTimeout and also emits a final summary.
func (m *Manager) logStats(ctx context.Context, start time.Time) {
	ticker := time.NewTicker(10 * time.Second)
	defer ticker.Stop()

	var lastBytesSent int64

	for {
		select {
		case <-ctx.Done():
			// Emit final summary regardless of elapsed time.
			snap := m.metrics.Snapshot()
			m.logger.Info("traffic session final stats",
				"dest", m.destAddr,
				"protocol", m.protocol,
				"elapsed_sec", time.Since(start).Round(time.Second).Seconds(),
				"bytes_sent_total", snap.BytesSent,
				"throughput_mbps", fmt.Sprintf("%.2f", snap.ThroughputBps/1e6),
				"active_sessions", snap.ActiveSessions,
			)
			return
		case <-ticker.C:
			snap := m.metrics.Snapshot()
			delta := snap.BytesSent - lastBytesSent
			lastBytesSent = snap.BytesSent
			m.logger.Info("traffic stats",
				"dest", m.destAddr,
				"protocol", m.protocol,
				"elapsed_sec", time.Since(start).Round(time.Second).Seconds(),
				"bytes_sent_total", snap.BytesSent,
				"bytes_sent_interval", delta,
				"throughput_mbps", fmt.Sprintf("%.2f", snap.ThroughputBps/1e6),
				"active_sessions", snap.ActiveSessions,
			)
			if snap.PacketsDropped > 0 {
				m.logger.Warn("packet drops detected",
					"dest", m.destAddr,
					"protocol", m.protocol,
					"packets_dropped", snap.PacketsDropped,
					"packets_sent", snap.PacketsSent,
					"loss_pct", fmt.Sprintf("%.2f%%", snap.PacketLossPct),
				)
			}
		}
	}
}

// runPool starts m.sessions goroutines concurrently and waits for all to finish.
func (m *Manager) runPool(ctx context.Context, lim *ratelimit.Limiter) error {
	m.logger.Info("starting session pool",
		"sessions", m.sessions,
		"dest", m.destAddr,
		"protocol", m.protocol,
	)
	var wg sync.WaitGroup
	for i := 0; i < m.sessions; i++ {
		wg.Add(1)
		go func(id int) {
			defer wg.Done()
			m.activeSessions.Add(1)
			defer m.activeSessions.Add(-1)
			m.metrics.IncrSessions()
			defer m.metrics.DecrSessions()

			if err := m.runSession(ctx, id, lim); err != nil {
				m.logger.Warn("session ended", "id", id, "err", err)
			}
		}(i)
	}
	wg.Wait()
	return nil
}

// runPoisson starts sessions according to a Poisson arrival process.
// New sessions are spawned as goroutines; the function returns when ctx expires.
func (m *Manager) runPoisson(ctx context.Context, pp *patterns.PoissonPattern, lim *ratelimit.Limiter) error {
	m.logger.Info("starting poisson session manager",
		"max_sessions", pp.MaxSessions,
		"dest", m.destAddr,
		"protocol", m.protocol,
	)
	var wg sync.WaitGroup
	sessionID := 0

	for {
		// Sample next inter-arrival time.
		wait := time.Duration(pp.NextInterArrivalSec() * float64(time.Second))

		select {
		case <-ctx.Done():
			wg.Wait()
			return nil
		case <-time.After(wait):
			// Enforce max concurrency.
			if int(m.activeSessions.Load()) >= pp.MaxSessions {
				continue
			}
			id := sessionID
			sessionID++
			wg.Add(1)
			go func(id int) {
				defer wg.Done()
				m.activeSessions.Add(1)
				defer m.activeSessions.Add(-1)
				m.metrics.IncrSessions()
				defer m.metrics.DecrSessions()

				// Each Poisson session runs for SessionDuration, not the full test.
				sessionCtx, cancel := context.WithTimeout(ctx, pp.SessionDuration())
				defer cancel()

				if err := m.runSession(sessionCtx, id, lim); err != nil {
					m.logger.Debug("poisson session ended", "id", id, "err", err)
				}
			}(id)
		}
	}
}

// runSession is the inner send loop for a single connection.
// It connects to destAddr and writes chunks of random data at the rate
// controlled by lim.  It reconnects automatically if the connection drops.
func (m *Manager) runSession(ctx context.Context, id int, lim *ratelimit.Limiter) error {
	buf := makePayload(lim.ChunkSize())

	for {
		select {
		case <-ctx.Done():
			return nil
		default:
		}

		conn, err := m.dial(ctx)
		if err != nil {
			// Could not connect — back off and retry.
			m.logger.Warn("dial failed, retrying", "session", id, "addr", m.destAddr, "err", err)
			select {
			case <-ctx.Done():
				return nil
			case <-time.After(2 * time.Second):
			}
			continue
		}

		m.logger.Debug("session connected", "id", id, "addr", m.destAddr)
		if err := m.sendLoop(ctx, conn, buf, lim); err != nil {
			m.logger.Debug("session send loop ended", "id", id, "err", err)
		}
		conn.Close()

		// If ctx is done, exit cleanly.
		select {
		case <-ctx.Done():
			return nil
		default:
			// Connection dropped unexpectedly — reconnect.
		}
	}
}

// sendLoop writes data to conn at the rate allowed by lim until ctx is done
// or an error occurs.
func (m *Manager) sendLoop(ctx context.Context, conn net.Conn, buf []byte, lim *ratelimit.Limiter) error {
	for {
		select {
		case <-ctx.Done():
			return nil
		default:
		}

		// Wait for rate-limiter token bucket.
		if err := lim.WaitBytes(ctx, len(buf)); err != nil {
			return err
		}

		n, err := conn.Write(buf)
		if n > 0 {
			m.metrics.AddBytesSent(int64(n))
		}
		if err != nil {
			return fmt.Errorf("write: %w", err)
		}
	}
}

func (m *Manager) dial(ctx context.Context) (net.Conn, error) {
	dialer := &net.Dialer{Timeout: 10 * time.Second}
	return dialer.DialContext(ctx, m.protocol, m.destAddr)
}

// makePayload creates a random-looking byte slice of size n.
// Random content prevents compression on intermediate devices and provides a
// more realistic traffic signature than all-zero payloads.
func makePayload(n int) []byte {
	buf := make([]byte, n)
	rand.Read(buf) //nolint:gosec
	return buf
}
