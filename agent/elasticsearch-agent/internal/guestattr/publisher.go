// Package guestattr publishes values as GCE guest attributes (namespace "byoc").
//
// The control plane reads them through the Compute API, which gives it the node's status
// even when the VM cannot reach the control plane over the network.
package guestattr

import (
	"bytes"
	"context"
	"fmt"
	"net/http"
	"strings"
	"time"
)

// Publisher writes guest attributes through the metadata server.
type Publisher struct {
	MetadataURL string
	HTTP        *http.Client
}

// Publish sets byoc/<key> to value.
func (p *Publisher) Publish(ctx context.Context, key string, value []byte) error {
	client := p.HTTP
	if client == nil {
		client = &http.Client{Timeout: 5 * time.Second}
	}
	endpoint := fmt.Sprintf("%s/instance/guest-attributes/byoc/%s", strings.TrimRight(p.MetadataURL, "/"), key)
	req, err := http.NewRequestWithContext(ctx, http.MethodPut, endpoint, bytes.NewReader(value))
	if err != nil {
		return err
	}
	req.Header.Set("Metadata-Flavor", "Google")
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		return fmt.Errorf("guest attribute %s: HTTP %d", key, resp.StatusCode)
	}
	return nil
}
