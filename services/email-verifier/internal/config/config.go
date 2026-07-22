package config

import (
	"os"
	"strconv"
	"time"
)

type Config struct {
	Address           string
	Token             string
	RedisURL          string
	ProxyURL          string
	SMTPEnabled       bool
	FromEmail         string
	HelloName         string
	RequestTimeout    time.Duration
	ConnectTimeout    time.Duration
	OperationTimeout  time.Duration
	MaxConcurrency    int
	DomainConcurrency int
}

func Load() Config {
	return Config{
		Address:           env("LISTEN_ADDRESS", ":8080"),
		Token:             os.Getenv("VERIFIER_TOKEN"),
		RedisURL:          os.Getenv("REDIS_URL"),
		ProxyURL:          os.Getenv("SMTP_PROXY_URL"),
		SMTPEnabled:       envBool("SMTP_ENABLED", true),
		FromEmail:         env("SMTP_FROM_EMAIL", "verify@example.org"),
		HelloName:         env("SMTP_HELLO_NAME", "localhost"),
		RequestTimeout:    envDuration("REQUEST_TIMEOUT", 25*time.Second),
		ConnectTimeout:    envDuration("SMTP_CONNECT_TIMEOUT", 8*time.Second),
		OperationTimeout:  envDuration("SMTP_OPERATION_TIMEOUT", 8*time.Second),
		MaxConcurrency:    envInt("MAX_CONCURRENCY", 10),
		DomainConcurrency: envInt("DOMAIN_CONCURRENCY", 1),
	}
}

func env(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}

func envBool(key string, fallback bool) bool {
	value := os.Getenv(key)
	if value == "" {
		return fallback
	}
	parsed, err := strconv.ParseBool(value)
	if err != nil {
		return fallback
	}
	return parsed
}

func envInt(key string, fallback int) int {
	value, err := strconv.Atoi(os.Getenv(key))
	if err != nil || value < 1 {
		return fallback
	}
	return value
}

func envDuration(key string, fallback time.Duration) time.Duration {
	value, err := time.ParseDuration(os.Getenv(key))
	if err != nil || value <= 0 {
		return fallback
	}
	return value
}
