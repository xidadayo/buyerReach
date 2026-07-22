package verifier

import (
	aftership "github.com/AfterShip/email-verifier"
	"testing"
	"time"
)

func TestMap(t *testing.T) {
	cases := []struct {
		name string
		raw  *aftership.Result
		want string
	}{
		{"syntax", &aftership.Result{Syntax: aftership.Syntax{Valid: false}}, "invalid"},
		{"disposable", &aftership.Result{Syntax: aftership.Syntax{Valid: true}, Disposable: true}, "disposable"},
		{"no mx", &aftership.Result{Syntax: aftership.Syntax{Valid: true}}, "invalid"},
		{"catch all", &aftership.Result{Syntax: aftership.Syntax{Valid: true}, HasMxRecords: true, Reachable: "unknown", SMTP: &aftership.SMTP{HostExists: true, CatchAll: true}}, "risky"},
		{"valid", &aftership.Result{Syntax: aftership.Syntax{Valid: true}, HasMxRecords: true, Reachable: "yes", SMTP: &aftership.SMTP{HostExists: true, Deliverable: true}}, "valid"},
		{"invalid mailbox", &aftership.Result{Syntax: aftership.Syntax{Valid: true}, HasMxRecords: true, Reachable: "no", SMTP: &aftership.SMTP{HostExists: true}}, "invalid"},
		{"unknown", &aftership.Result{Syntax: aftership.Syntax{Valid: true}, HasMxRecords: true, Reachable: "unknown"}, "unknown"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := Map(tc.raw, true, time.Millisecond)
			if got.Result != tc.want {
				t.Fatalf("got %s want %s", got.Result, tc.want)
			}
		})
	}
}
