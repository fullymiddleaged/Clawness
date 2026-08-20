"""Intentionally vulnerable fixture for tests/test_scan.py. Not imported/run."""
import hashlib
import os
import pickle
import random
import sqlite3
import subprocess

import requests
import yaml
from flask import request, send_file

AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
db_password = "hunter2supersecret"


def lookup(cur: sqlite3.Cursor, user_id):
    # sql-injection: f-string into execute
    cur.execute(f"SELECT * FROM users WHERE id = {user_id}")


def run(name):
    # command-injection: os.system + shell=True
    os.system("echo " + name)
    subprocess.run("ls " + name, shell=True)


def load(blob):
    # unsafe-deserialization: pickle.loads + yaml.load without SafeLoader
    obj = pickle.loads(blob)
    cfg = yaml.load(blob)
    return obj, cfg


def compute(expr):
    # code-eval
    return eval(expr)


def read_file():
    # path-traversal: open fed request data
    return open(request.args["path"]).read()


def hash_pw(pw):
    # weak-crypto
    return hashlib.md5(pw.encode()).hexdigest()


def token():
    # weak-crypto: random for a token
    return random.randint(1000, 9999)


def proxy():
    # ssrf: outbound request to request-derived URL
    return requests.get(request.args["url"])


def download():
    return send_file(request.args.get("f"))
