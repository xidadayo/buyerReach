package verifier

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	aftership "github.com/AfterShip/email-verifier"
	"github.com/redis/go-redis/v9"

	"buyerreach/email-verifier/internal/config"
)

type Request struct {
	Email string `json:"email"`
	SMTP  bool   `json:"smtp"`
}

type Result struct {
	Provider          string `json:"provider"`
	AdapterVersion    string `json:"adapter_version"`
	Result            string `json:"result"`
	Score             int    `json:"score"`
	IsCatchAll        bool   `json:"is_catch_all"`
	IsDisposable      bool   `json:"is_disposable"`
	IsRoleAccount     bool   `json:"is_role_account"`
	IsFreeProvider    bool   `json:"is_free_provider"`
	DomainDeliverable bool   `json:"domain_deliverable"`
	MailboxExists     bool   `json:"mailbox_exists"`
	SMTPCheck         bool   `json:"smtp_check"`
	Reason            string `json:"reason"`
	RawStatus         string `json:"raw_status"`
	DurationMS        int64  `json:"duration_ms"`
	Cached            bool   `json:"cached"`
	Suggestion        string `json:"suggestion,omitempty"`
}

type Engine interface {
	Verify(email string) (*aftership.Result, error)
}

func NewAfterShipEngine(cfg config.Config) Engine {
	v := aftership.NewVerifier().EnableCatchAllCheck().EnableDomainSuggest().
		FromEmail(cfg.FromEmail).HelloName(cfg.HelloName).
		ConnectTimeout(cfg.ConnectTimeout).OperationTimeout(cfg.OperationTimeout)
	if cfg.SMTPEnabled {
		v.EnableSMTPCheck()
	}
	if cfg.ProxyURL != "" {
		v.Proxy(cfg.ProxyURL)
	}
	// Auto-update is intentionally not enabled: production builds must be deterministic.
	return v
}

type Metrics struct {
	Requests  atomic.Uint64
	CacheHits atomic.Uint64
	Failures  atomic.Uint64
	Unknown   atomic.Uint64
}

type Service struct {
	engine      Engine
	redis       *redis.Client
	timeout     time.Duration
	sem         chan struct{}
	domainLimit int
	localMu     sync.Mutex
	localDomain map[string]int
	Metrics     Metrics
}

func NewService(engine Engine, cfg config.Config) (*Service, error) {
	var client *redis.Client
	if cfg.RedisURL != "" {
		options, err := redis.ParseURL(cfg.RedisURL)
		if err != nil {
			return nil, fmt.Errorf("invalid Redis URL: %w", err)
		}
		client = redis.NewClient(options)
	}
	return &Service{engine: engine, redis: client, timeout: cfg.RequestTimeout,
		sem: make(chan struct{}, cfg.MaxConcurrency), domainLimit: cfg.DomainConcurrency,
		localDomain: map[string]int{}}, nil
}

func (s *Service) Ready(ctx context.Context) error {
	if s.redis == nil {
		return nil
	}
	return s.redis.Ping(ctx).Err()
}

func (s *Service) Verify(ctx context.Context, request Request) (Result, error) {
	s.Metrics.Requests.Add(1)
	email := strings.ToLower(strings.TrimSpace(request.Email))
	if email == "" {
		return Result{}, errors.New("email is required")
	}
	key := hash(email)
	if cached, ok := s.cached(ctx, key); ok {
		s.Metrics.CacheHits.Add(1)
		return cached, nil
	}

	lockKey := "emailverify:lock:" + key
	locked := false
	if s.redis != nil {
		var err error
		locked, err = s.redis.SetNX(ctx, lockKey, "1", s.timeout+5*time.Second).Result()
		if err != nil {
			return Result{}, fmt.Errorf("Redis lock failed: %w", err)
		}
		if !locked {
			for i := 0; i < 20; i++ {
				select {
				case <-ctx.Done():
					return Result{}, ctx.Err()
				case <-time.After(100 * time.Millisecond):
				}
				if cached, ok := s.cached(ctx, key); ok {
					s.Metrics.CacheHits.Add(1)
					return cached, nil
				}
			}
			return Result{}, errors.New("verification already in progress")
		}
		defer s.redis.Del(context.Background(), lockKey)
	}

	domain := domainOf(email)
	if !s.acquireDomain(ctx, domain) {
		return Result{}, errors.New("domain concurrency limit reached")
	}
	defer s.releaseDomain(domain)
	select {
	case s.sem <- struct{}{}:
		defer func() { <-s.sem }()
	case <-ctx.Done():
		return Result{}, ctx.Err()
	}

	started := time.Now()
	type response struct {
		result *aftership.Result
		err    error
	}
	resultCh := make(chan response, 1)
	go func() { ret, err := s.engine.Verify(email); resultCh <- response{ret, err} }()
	var raw *aftership.Result
	select {
	case <-ctx.Done():
		s.Metrics.Failures.Add(1)
		return Result{}, ctx.Err()
	case item := <-resultCh:
		if item.err != nil {
			s.Metrics.Failures.Add(1)
			slog.Warn("verification engine failed", "domain", domain, "error", item.err.Error())
			return unknownResult(time.Since(started), "verification_error"), nil
		}
		raw = item.result
	}
	result := Map(raw, request.SMTP, time.Since(started))
	if result.Result == "unknown" {
		s.Metrics.Unknown.Add(1)
	}
	s.store(ctx, key, result)
	return result, nil
}

func Map(raw *aftership.Result, smtpRequested bool, elapsed time.Duration) Result {
	result := Result{Provider: "aftership-local", AdapterVersion: "v1", Result: "unknown", Score: 30,
		Reason: "inconclusive", RawStatus: "unknown", DurationMS: elapsed.Milliseconds()}
	if raw == nil {
		return result
	}
	result.IsDisposable, result.IsRoleAccount, result.IsFreeProvider = raw.Disposable, raw.RoleAccount, raw.Free
	result.DomainDeliverable, result.Suggestion, result.RawStatus = raw.HasMxRecords, raw.Suggestion, raw.Reachable
	result.SMTPCheck = smtpRequested && raw.SMTP != nil
	if raw.SMTP != nil {
		result.IsCatchAll, result.MailboxExists = raw.SMTP.CatchAll, raw.SMTP.Deliverable
	}
	switch {
	case !raw.Syntax.Valid:
		result.Result, result.Score, result.Reason = "invalid", 0, "invalid_syntax"
	case raw.Disposable:
		result.Result, result.Score, result.Reason = "disposable", 0, "disposable_domain"
	case !raw.HasMxRecords:
		result.Result, result.Score, result.Reason = "invalid", 0, "no_mx_records"
	case result.IsCatchAll:
		result.Result, result.Score, result.Reason = "risky", 45, "catch_all"
	case raw.Reachable == "yes" && result.MailboxExists:
		result.Result, result.Score, result.Reason = "valid", 90, "smtp_recipient_accepted"
	case raw.Reachable == "no" && raw.SMTP != nil && raw.SMTP.HostExists:
		result.Result, result.Score, result.Reason = "invalid", 5, "smtp_recipient_rejected"
	default:
		result.Result, result.Score, result.Reason = "unknown", 30, "smtp_inconclusive"
	}
	return result
}

func unknownResult(elapsed time.Duration, reason string) Result {
	return Result{Provider: "aftership-local", AdapterVersion: "v1", Result: "unknown", Score: 20, Reason: reason, RawStatus: "unknown", DurationMS: elapsed.Milliseconds()}
}

func (s *Service) cached(ctx context.Context, key string) (Result, bool) {
	if s.redis == nil {
		return Result{}, false
	}
	value, err := s.redis.Get(ctx, "emailverify:result:"+key).Bytes()
	if err != nil {
		return Result{}, false
	}
	var result Result
	if json.Unmarshal(value, &result) != nil {
		return Result{}, false
	}
	result.Cached = true
	return result, true
}

func (s *Service) store(ctx context.Context, key string, result Result) {
	if s.redis == nil || result.Result == "unknown" {
		return
	}
	ttl := 7 * 24 * time.Hour
	if result.Result == "valid" {
		ttl = 30 * 24 * time.Hour
	}
	if result.Result == "risky" {
		ttl = 7 * 24 * time.Hour
	}
	value, _ := json.Marshal(result)
	_ = s.redis.Set(ctx, "emailverify:result:"+key, value, ttl).Err()
}

func (s *Service) acquireDomain(ctx context.Context, domain string) bool {
	if s.redis != nil {
		key := "emailverify:limit:domain:" + domain
		count, err := s.redis.Incr(ctx, key).Result()
		if err != nil { return false }
		if count == 1 { _ = s.redis.Expire(ctx, key, s.timeout+5*time.Second).Err() }
		if count > int64(s.domainLimit) { _ = s.redis.Decr(ctx, key).Err(); return false }
		return true
	}
	s.localMu.Lock()
	defer s.localMu.Unlock()
	if s.localDomain[domain] >= s.domainLimit {
		return false
	}
	s.localDomain[domain]++
	return true
}
func (s *Service) releaseDomain(domain string) {
	if s.redis != nil {
		key := "emailverify:limit:domain:" + domain
		value, err := s.redis.Decr(context.Background(), key).Result()
		if err == nil && value <= 0 { _ = s.redis.Del(context.Background(), key).Err() }
		return
	}
	s.localMu.Lock()
	defer s.localMu.Unlock()
	s.localDomain[domain]--
	if s.localDomain[domain] <= 0 {
		delete(s.localDomain, domain)
	}
}
func domainOf(email string) string {
	parts := strings.SplitN(email, "@", 2)
	if len(parts) == 2 {
		return parts[1]
	}
	return "invalid"
}
func hash(value string) string {
	sum := sha256.Sum256([]byte(value))
	return hex.EncodeToString(sum[:])
}
