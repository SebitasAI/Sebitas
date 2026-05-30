"""HTTP routers exposed to the Misterr web app (Bearer-JWT authenticated).

These are distinct from `app/web_api.py` (server-to-server with a shared
secret) -- the routers here authenticate the end user directly via Clerk JWT
and apply per-user / per-workspace permissions on every call.
"""
