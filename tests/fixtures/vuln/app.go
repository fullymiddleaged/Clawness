// Intentionally vulnerable fixture for tests/test_scan.py. Not compiled/run.
package main

import (
	"crypto/md5"
	"database/sql"
	"fmt"
	"html/template"
	"math/rand"
	"net/http"
	"os"
	"os/exec"
)

var apiKey = "sk_live_0123456789abcdef0123" // hardcoded-secret

func lookup(db *sql.DB, r *http.Request) {
	id := r.URL.Query().Get("id")
	// sql-injection: Sprintf into Query
	db.Query(fmt.Sprintf("SELECT * FROM users WHERE id = %s", id))
}

func run(name string) {
	// command-injection: exec.Command invoking a shell
	exec.Command("bash", "-c", "echo "+name).Run()
}

func render(s string) {
	// xss: template.HTML marks a string safe, bypassing escaping
	_ = template.HTML(s)
}

func read(r *http.Request) {
	// path-traversal: os.Open fed request data
	os.Open(r.URL.Query().Get("path"))
}

func weak(pw string) [16]byte {
	// weak-crypto: md5
	return md5.Sum([]byte(pw))
}

func token() int {
	// weak-crypto: math/rand
	return rand.Intn(9999)
}

func proxy(r *http.Request) {
	// ssrf: outbound request to request-derived URL
	http.Get(r.URL.Query().Get("url"))
}
