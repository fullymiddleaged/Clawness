<?php
// A clean fixture — the enumerator must find NOTHING here. Not served/run.

$apiKey = getenv("API_KEY"); // reference, not a literal

function lookup($db) {
    // parameterised (prepared) query — the safe form
    $stmt = $db->prepare("SELECT * FROM users WHERE id = ?");
    $stmt->execute([$_GET['id']]);
    return $stmt;
}
