// Intentionally vulnerable fixture for tests/test_scan.py. Not imported/run.
import React from "react";

const GITHUB_TOKEN = "ghp_0123456789abcdefABCDEF0123456789abcd";

export function Comment({ html, req }) {
  // xss: dangerouslySetInnerHTML + innerHTML assignment
  document.getElementById("x").innerHTML = html;
  return <div dangerouslySetInnerHTML={{ __html: html }} />;
}

export function runUserCode(src) {
  // code-eval
  return eval(src);
}

export function query(db, name) {
  // sql-injection: template literal interpolation
  return db.query(`SELECT * FROM t WHERE name = '${name}'`);
}

export function token() {
  // weak-crypto: Math.random for a token
  return Math.random().toString(36);
}
