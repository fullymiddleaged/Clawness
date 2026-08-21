# A clean fixture — the enumerator must find NOTHING here. Not required/run.
require "digest"

API_KEY = ENV["API_KEY"] # reference, not a literal

def lookup(user_id)
  # parameterised query — the safe form
  User.where("id = ?", user_id)
end

def hash_pw(pw)
  Digest::SHA256.hexdigest(pw) # strong hash
end
