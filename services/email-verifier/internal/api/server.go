package api

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"strings"
	"time"

	"buyerreach/email-verifier/internal/verifier"
)

type Server struct {
	service *verifier.Service
	token   string
	timeout time.Duration
	mux     *http.ServeMux
}

func New(service *verifier.Service, token string, timeout time.Duration) *Server {
	s := &Server{service: service, token: token, timeout: timeout, mux: http.NewServeMux()}
	s.mux.HandleFunc("GET /health", s.health)
	s.mux.HandleFunc("GET /ready", s.auth(s.ready))
	s.mux.HandleFunc("GET /metrics", s.auth(s.metrics))
	s.mux.HandleFunc("POST /v1/verify", s.auth(s.verify))
	return s
}

func (s *Server) Handler() http.Handler { return http.MaxBytesHandler(s.mux, 16<<10) }
func (s *Server) health(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, 200, map[string]string{"status": "ok"})
}
func (s *Server) ready(w http.ResponseWriter, r *http.Request) {
	if err := s.service.Ready(r.Context()); err != nil {
		writeJSON(w, 503, map[string]string{"status": "not_ready", "error": "redis_unavailable"})
		return
	}
	writeJSON(w, 200, map[string]string{"status": "ready"})
}
func (s *Server) metrics(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "text/plain; version=0.0.4")
	fmt.Fprintf(w, "buyerreach_email_verifier_requests_total %d\nbuyerreach_email_verifier_cache_hits_total %d\nbuyerreach_email_verifier_failures_total %d\nbuyerreach_email_verifier_unknown_total %d\n", s.service.Metrics.Requests.Load(), s.service.Metrics.CacheHits.Load(), s.service.Metrics.Failures.Load(), s.service.Metrics.Unknown.Load())
}

func (s *Server) verify(w http.ResponseWriter, r *http.Request) {
	var request verifier.Request
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&request); err != nil {
		writeJSON(w, 400, map[string]string{"error": "invalid_request"})
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), s.timeout)
	defer cancel()
	result, err := s.service.Verify(ctx, request)
	if err != nil {
		slog.Warn("verification failed", "email_hash", emailHash(request.Email), "domain", domain(request.Email), "error_code", safeError(err))
		writeJSON(w, 503, map[string]string{"error": safeError(err)})
		return
	}
	slog.Info("verification completed", "email_hash", emailHash(request.Email), "domain", domain(request.Email), "result", result.Result, "duration_ms", result.DurationMS, "cached", result.Cached)
	writeJSON(w, 200, result)
}

func (s *Server) auth(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		provided := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		if s.token == "" || len(provided) != len(s.token) || subtle.ConstantTimeCompare([]byte(provided), []byte(s.token)) != 1 {
			writeJSON(w, 401, map[string]string{"error": "unauthorized"})
			return
		}
		next(w, r)
	}
}
func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}
func emailHash(email string) string {
	sum := sha256.Sum256([]byte(strings.ToLower(strings.TrimSpace(email))))
	return hex.EncodeToString(sum[:])
}
func domain(email string) string {
	parts := strings.SplitN(email, "@", 2)
	if len(parts) == 2 {
		return strings.ToLower(parts[1])
	}
	return "invalid"
}
func safeError(err error) string {
	if err == context.DeadlineExceeded || err == context.Canceled {
		return "timeout"
	}
	text := err.Error()
	if strings.Contains(text, "concurrency") {
		return "rate_limited"
	}
	if strings.Contains(text, "Redis") {
		return "redis_unavailable"
	}
	return "verification_unavailable"
}
