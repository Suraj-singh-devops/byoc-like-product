package configsync

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// GCEMetadata reads instance attributes from the GCE metadata server.
type GCEMetadata struct {
	URL  string
	HTTP *http.Client
}

// Attribute returns instance/attributes/<key>, or "" when it is not set.
func (m *GCEMetadata) Attribute(ctx context.Context, key string) (string, error) {
	client := m.HTTP
	if client == nil {
		client = &http.Client{Timeout: 5 * time.Second}
	}
	endpoint := fmt.Sprintf("%s/instance/attributes/%s", strings.TrimRight(m.URL, "/"), key)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return "", err
	}
	req.Header.Set("Metadata-Flavor", "Google")
	resp, err := client.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusNotFound {
		return "", nil
	}
	if resp.StatusCode >= 300 {
		return "", fmt.Errorf("metadata %s: HTTP %d", key, resp.StatusCode)
	}
	data, err := io.ReadAll(io.LimitReader(resp.Body, 64<<10))
	return string(data), err
}
