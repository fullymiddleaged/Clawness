// A clean fixture — the enumerator must find NOTHING here. Not compiled/run.
package main

import (
	"database/sql"
	"os"
)

var apiKey = os.Getenv("API_KEY") // reference, not a literal

func lookup(db *sql.DB, id string) {
	// parameterised query — the safe form, must not trip sql-injection
	db.Query("SELECT * FROM users WHERE id = ?", id)
}

func read(name string) (*os.File, error) {
	// a constant prefix, no request data → not traversal
	return os.Open("/etc/config/" + name)
}
