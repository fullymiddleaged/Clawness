# Intentionally vulnerable fixture for tests/test_scan.py. Not required/run.
require "digest"
require "yaml"

API_KEY = "sk_live_abcdef0123456789xyz" # hardcoded-secret

def lookup(user_id)
  # sql-injection: interpolation into where
  User.where("id = #{user_id}")
end

def run(name)
  # command-injection
  system("echo #{name}")
end

def shell(name)
  # command-injection: backticks with interpolation
  `ls #{name}`
end

def load(blob)
  # unsafe-deserialization
  YAML.load(blob)
end

def compute(expr)
  # code-eval
  eval(expr)
end

def render(html)
  # xss
  html.html_safe
end

def read_file(params)
  # path-traversal: File.read fed request data
  File.read(params[:path])
end

def hash_pw(pw)
  # weak-crypto
  Digest::MD5.hexdigest(pw)
end

def proxy(params)
  # ssrf
  Net::HTTP.get(URI(params[:url]))
end
