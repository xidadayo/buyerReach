package main

import (
	"context"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"buyerreach/email-verifier/internal/api"
	"buyerreach/email-verifier/internal/config"
	"buyerreach/email-verifier/internal/verifier"
)

func main() {
	cfg := config.Load()
	if cfg.Token == "" {
		slog.Error("VERIFIER_TOKEN is required")
		os.Exit(1)
	}
	service, err := verifier.NewService(verifier.NewAfterShipEngine(cfg), cfg)
	if err != nil {
		slog.Error("configuration error", "error_code", "invalid_config")
		os.Exit(1)
	}
	httpServer := &http.Server{Addr: cfg.Address, Handler: api.New(service, cfg.Token, cfg.RequestTimeout).Handler(), ReadHeaderTimeout: 5 * time.Second, ReadTimeout: cfg.RequestTimeout + time.Second, WriteTimeout: cfg.RequestTimeout + time.Second, IdleTimeout: 60 * time.Second}
	go func() {
		slog.Info("email verifier listening", "address", cfg.Address)
		if err := httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			slog.Error("server failed", "error", err)
			os.Exit(1)
		}
	}()
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	<-stop
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_ = httpServer.Shutdown(ctx)
}
