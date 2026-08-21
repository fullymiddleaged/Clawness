<?php
// Intentionally vulnerable fixture for tests/test_scan.py. Not served/run.

$apiKey = "sk_live_0123456789abcdef"; // hardcoded-secret

function lookup($db) {
    // sql-injection: concatenated request data into query
    return $db->query("SELECT * FROM users WHERE id = " . $_GET['id']);
}

function run($name) {
    // command-injection
    system("echo " . $name);
}

function load($blob) {
    // unsafe-deserialization
    return unserialize($blob);
}

function compute($expr) {
    // code-eval
    return eval($expr);
}

function render() {
    // xss: echo request data
    echo $_GET['q'];
}

function read_page() {
    // path-traversal: include request data (LFI/RFI)
    include($_GET['page']);
}

function token() {
    // weak-crypto: mt_rand
    return mt_rand();
}

function weak($pw) {
    // weak-crypto: md5
    return md5($pw);
}

function proxy() {
    // ssrf
    $ch = curl_init();
    curl_setopt($ch, CURLOPT_URL, $_GET['url']);
}
