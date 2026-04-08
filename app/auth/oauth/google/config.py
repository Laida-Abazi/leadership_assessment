"""
OAuth configuration using environment variables.
"""
import os

# Google OAuth (from env)
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")

# Session secret for Authlib (secure random key in production)
SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY", os.getenv("JWT_SECRET_KEY", "change-me-session-secret"))

# Google OpenID Connect
GOOGLE_METADATA_URL = "https://accounts.google.com/.well-known/openid-configuration"
GOOGLE_SCOPES = "openid email profile"
