package api

import (
	"buyerreach/email-verifier/internal/config"
	"buyerreach/email-verifier/internal/verifier"
	aftership "github.com/AfterShip/email-verifier"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

type fakeEngine struct{}

func (fakeEngine) Verify(string) (*aftership.Result, error) {
	return &aftership.Result{Syntax: aftership.Syntax{Valid: true}, HasMxRecords: true, Reachable: "yes", SMTP: &aftership.SMTP{HostExists: true, Deliverable: true}}, nil
}

func testServer(t *testing.T) *Server {
	t.Helper()
	cfg := config.Config{RequestTimeout: time.Second, MaxConcurrency: 2, DomainConcurrency: 1}
	service, err := verifier.NewService(fakeEngine{}, cfg)
	if err != nil {
		t.Fatal(err)
	}
	return New(service, "secret", time.Second)
}
func TestHealthDoesNotRequireAuth(t *testing.T) {
	s := testServer(t)
	r := httptest.NewRequest(http.MethodGet, "/health", nil)
	w := httptest.NewRecorder()
	s.Handler().ServeHTTP(w, r)
	if w.Code != 200 {
		t.Fatalf("got %d", w.Code)
	}
}
func TestVerifyRequiresAuth(t *testing.T) {
	s := testServer(t)
	r := httptest.NewRequest(http.MethodPost, "/v1/verify", strings.NewReader(`{"email":"a@example.com","smtp":true}`))
	w := httptest.NewRecorder()
	s.Handler().ServeHTTP(w, r)
	if w.Code != 401 {
		t.Fatalf("got %d", w.Code)
	}
}

func TestReadyRequiresAuth(t *testing.T) {
	s := testServer(t)
	r := httptest.NewRequest(http.MethodGet, "/ready", nil)
	w := httptest.NewRecorder()
	s.Handler().ServeHTTP(w, r)
	if w.Code != 401 { t.Fatalf("got %d", w.Code) }
}
func TestVerifyReturnsMappedResult(t *testing.T) {
	s := testServer(t)
	r := httptest.NewRequest(http.MethodPost, "/v1/verify", strings.NewReader(`{"email":"a@example.com","smtp":true}`))
	r.Header.Set("Authorization", "Bearer secret")
	w := httptest.NewRecorder()
	s.Handler().ServeHTTP(w, r)
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"result":"valid"`) {
		t.Fatalf("got %d %s", w.Code, w.Body.String())
	}
}
